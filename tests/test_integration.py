"""Integration tests for the Corpus ↔ Core bridge."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from verity_core.corpus import load_corpus
from verity_core.scorecard import Scorecard

from verity_corpus.fetcher import fetch, is_fetched
from verity_corpus.models.manifest import DomainTag, ManifestEntry, SourceSpec
from verity_corpus.registry import CorpusRegistry
from verity_corpus.resolver import core_manifest
from verity_corpus.results import record_vrc_entry, sync_status, update_scorecard_from_core
from verity_corpus.scorecard_store import exists, load, save

pytestmark = pytest.mark.integration


def _entry(
    tmp_path: Path,
    *,
    name: str = "pipe-env",
    adapter: str = "terminal",
    extra_config: dict | None = None,
) -> ManifestEntry:
    env_root = tmp_path / "env" / name
    env_root.mkdir(parents=True, exist_ok=True)
    return ManifestEntry.create(
        name=name,
        source=SourceSpec(type="local", path=str(env_root)),
        domain=DomainTag(category="terminal", subcategory="bash"),
        adapter=adapter,
        adapter_config={"image": "busybox:latest", "timeout": 30, **(extra_config or {})},
    )


class TestManifestFetchCoreManifest:
    def test_pipeline_produces_load_env_fields(self, tmp_path: Path) -> None:
        entry = _entry(tmp_path)
        cache = tmp_path / "cache"
        env_root = fetch(entry, cache_dir=cache)
        assert is_fetched(entry, cache_dir=cache) is True
        assert env_root == Path(entry.source.path)
        payload = core_manifest(entry, env_root)
        for key in ("id", "format", "domain", "source", "commit", "instructions"):
            assert key in payload, key
        assert payload["id"] == entry.id
        assert payload["format"] == "terminal"
        assert payload["domain"] == "tool_use"
        assert payload["image"] == "busybox:latest"
        assert payload["timeout"] == 30


class TestScorecardStoreAndMerge:
    def test_core_scorecard_round_trip_preserves_none_vs_zero(self, tmp_path: Path) -> None:
        card = Scorecard(env_id="env1")
        card.set_axis("V1", 0.0, "verity-signal", {"n": 4})
        save(card, tmp_path)
        loaded = load("env1", tmp_path)
        assert loaded.get_axis("V1").value == 0.0
        assert loaded.get_axis("V2").value is None
        assert loaded.get_axis("V1").tool == "verity-signal"

    def test_merge_keeps_unrelated_axes(self, tmp_path: Path) -> None:
        first = Scorecard(env_id="env-merge")
        first.set_axis("V1", 1.0, "verity-redteam")
        update_scorecard_from_core("env-merge", first, tmp_path)

        second = Scorecard(env_id="env-merge")
        second.set_axis("U2", 0.0, "verity-signal")
        merged = update_scorecard_from_core("env-merge", second, tmp_path)

        assert merged.get_axis("V1").value == 1.0
        assert merged.get_axis("V1").tool == "verity-redteam"
        assert merged.get_axis("U2").value == 0.0
        assert merged.get_axis("U2").tool == "verity-signal"
        assert merged.get_axis("V3").value is None
        assert exists("env-merge", tmp_path)


class TestVRCAndStatusSync:
    def test_record_vrc_entry_writes_nested_json(self, tmp_path: Path) -> None:
        entry = record_vrc_entry(
            env_id="env1",
            exploit_type="reward_hack",
            trajectory=[{"action": "cat tests/run-tests.sh"}],
            model_id="gpt-test",
            hackability_curve={1: 0.2, 8: 0.83},
            vrc_dir=tmp_path,
            notes="integration",
        )
        path = tmp_path / "env1" / f"{entry.id}.json"
        assert path.is_file()
        assert entry.exploit_type == "reward_hack"
        assert entry.hackability_curve == {1: 0.2, 8: 0.83}

    def test_sync_status_marks_scored_entries_audited(self, tmp_path: Path) -> None:
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        scorecards = tmp_path / "scorecards"
        entry = ManifestEntry.create(
            name="to-audit",
            source=SourceSpec(
                type="git",
                url="https://github.com/example/env",
                commit="abc",
                path=".",
            ),
            domain=DomainTag(category="code"),
            adapter="docker_test",
        )
        registry = CorpusRegistry(manifests)
        registry.add_entry(entry, "manual.yaml")
        assert registry.by_id(entry.id).status == "registered"

        card = Scorecard(env_id=entry.id)
        card.set_axis("V1", 0.5, "verity-redteam")
        save(card, scorecards)

        changed = sync_status(registry, scorecards)
        assert changed == {entry.id: "audited"}
        assert registry.by_id(entry.id).status == "audited"
        on_disk = yaml.safe_load((manifests / "manual.yaml").read_text(encoding="utf-8"))
        assert on_disk["entries"][0]["status"] == "registered"


class TestExportForCore:
    def test_exported_yaml_loads_with_core_load_corpus(self, tmp_path: Path) -> None:
        manifests = tmp_path / "manifests"
        manifests.mkdir()
        registry = CorpusRegistry(manifests)
        entry = ManifestEntry.create(
            name="export-me",
            source=SourceSpec(
                type="git",
                url="https://github.com/example/env",
                commit="deadbeef",
                path="tasks/foo",
            ),
            domain=DomainTag(category="terminal", subcategory="bash"),
            adapter="terminal",
            adapter_config={"image": "busybox:latest", "timeout": 90},
            metadata={"instructions": "do the task"},
        )
        registry.add_entry(entry, "bench.yaml")

        export_dir = tmp_path / "core-export"
        written = registry.export_for_core(export_dir)
        assert written
        loaded = load_corpus(export_dir)
        assert len(loaded) == 1
        row = loaded[0]
        assert row["id"] == entry.id
        assert row["format"] == "terminal"
        assert row["domain"] == "tool_use"
        assert row["source"] == "https://github.com/example/env"
        assert row["commit"] == "deadbeef"
        assert row["image"] == "busybox:latest"
        assert row["instructions"] == "do the task"
