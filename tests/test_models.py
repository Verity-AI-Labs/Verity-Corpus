"""Tests for manifest and VRC data models."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from verity_corpus.models import (
    DomainTag,
    ManifestEntry,
    SourceSpec,
    VRCEntry,
    compute_entry_id,
)


def _source(**overrides: object) -> SourceSpec:
    payload: dict = {
        "type": "git",
        "url": "https://github.com/example/env",
        "commit": "abc123",
        "path": "tasks/foo",
    }
    payload.update(overrides)
    return SourceSpec.model_validate(payload)


def _domain() -> DomainTag:
    return DomainTag(category="terminal", subcategory="bash")


class TestManifestEntryId:
    def test_same_inputs_produce_same_id(self) -> None:
        a = ManifestEntry.create(
            name="one", source=_source(), domain=_domain(), adapter="terminal"
        )
        b = ManifestEntry.create(
            name="two", source=_source(), domain=_domain(), adapter="docker_test"
        )
        assert a.id == b.id
        assert a.id == compute_entry_id(_source())
        assert len(a.id) == 12

    def test_different_inputs_produce_different_id(self) -> None:
        a = ManifestEntry.create(
            name="one", source=_source(path="a"), domain=_domain(), adapter="terminal"
        )
        b = ManifestEntry.create(
            name="one", source=_source(path="b"), domain=_domain(), adapter="terminal"
        )
        assert a.id != b.id

    def test_unpinned_commit_hashes_as_head(self) -> None:
        unpinned = _source(commit=None)
        explicit_head = _source(commit="HEAD")
        assert compute_entry_id(unpinned) == compute_entry_id(explicit_head)

    def test_create_sets_added_at_and_computes_id(self) -> None:
        before = datetime.now(UTC)
        entry = ManifestEntry.create(
            name="env", source=_source(), domain=_domain(), adapter="terminal"
        )
        after = datetime.now(UTC)
        assert entry.id == compute_entry_id(entry.source)
        assert before <= entry.added_at <= after
        assert entry.status == "registered"

    def test_constructor_overwrites_supplied_id(self) -> None:
        entry = ManifestEntry(
            id="not-the-real-id",
            name="env",
            source=_source(),
            domain=_domain(),
            adapter="terminal",
        )
        assert entry.id == compute_entry_id(entry.source)
        assert entry.id != "not-the-real-id"


class TestVRCEntry:
    def test_create_assigns_uuid4(self) -> None:
        entry = VRCEntry(
            env_id="env1",
            exploit_type="reward_hack",
            trajectory=[{"action": "ls", "observation": "ok"}],
            model_id="gpt-test",
        )
        parsed = UUID(entry.id)
        assert parsed.version == 4
        assert entry.notes == ""
        assert entry.hackability_curve is None

    def test_save_creates_subdirectory(self, tmp_path: Path) -> None:
        entry = VRCEntry(
            env_id="env1",
            exploit_type="verifier_bypass",
            trajectory=[],
            model_id="gpt-test",
            hackability_curve={1: 0.2, 2: 0.36},
        )
        path = entry.save(tmp_path)
        assert path == tmp_path / "env1" / f"{entry.id}.json"
        assert path.is_file()
        loaded = VRCEntry.load(tmp_path, entry.id)
        assert loaded.id == entry.id
        assert loaded.env_id == "env1"
        assert loaded.hackability_curve == {1: 0.2, 2: 0.36}
