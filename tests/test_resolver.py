"""Tests for core_manifest instruction resolution."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from verity_corpus.fetcher import COMMIT_MARKER, is_fetched, repo_cache_dir
from verity_corpus.models.manifest import DomainTag, ManifestEntry, SourceSpec
from verity_corpus.registry import CorpusRegistry
from verity_corpus.resolver import MissingInstructionsWarning, core_manifest, resolve


def _entry(
    *,
    name: str = "task-7",
    metadata: dict | None = None,
    path: str = "tasks/7",
) -> ManifestEntry:
    return ManifestEntry.create(
        name=name,
        source=SourceSpec(
            type="git",
            url="https://github.com/example/bench",
            commit="abc123",
            path=path,
        ),
        domain=DomainTag(category="terminal", subcategory="bash"),
        adapter="terminal",
        adapter_config={"image": "busybox:latest"},
        metadata=metadata or {},
    )


class TestCoreManifestInstructions:
    def test_metadata_instructions_win_over_file(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        env_root = tmp_path / "env"
        env_root.mkdir()
        (env_root / "instruction.md").write_text("file prose that must not be used", encoding="utf-8")
        entry = _entry(metadata={"instructions": "  do the inline task  "})

        payload = core_manifest(entry, env_root)

        assert payload["instructions"] == "do the inline task"
        assert "NO TASK INSTRUCTIONS" not in caplog.text

    def test_reads_instruction_file_when_metadata_absent(self, tmp_path: Path) -> None:
        env_root = tmp_path / "env"
        env_root.mkdir()
        (env_root / "instruction.md").write_text(
            "Redirect stdout and stderr into output1.txt.\n",
            encoding="utf-8",
        )
        entry = _entry(name="7")

        payload = core_manifest(entry, env_root)

        assert payload["instructions"] == "Redirect stdout and stderr into output1.txt."
        assert payload["instructions"] != entry.name

    def test_reads_instructions_md_alias_without_source_hardcoding(self, tmp_path: Path) -> None:
        env_root = tmp_path / "env"
        env_root.mkdir()
        (env_root / "instructions.md").write_text(
            "Write a parser for the given grammar.",
            encoding="utf-8",
        )
        entry = _entry(name="trace-game")

        payload = core_manifest(entry, env_root)

        assert payload["instructions"] == "Write a parser for the given grammar."

    def test_falls_back_to_name_and_warns_when_both_absent(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        env_root = tmp_path / "env"
        env_root.mkdir()
        (env_root / "README.md").write_text("not the task prompt", encoding="utf-8")
        entry = _entry(name="5")

        with pytest.warns(MissingInstructionsWarning, match="NO TASK INSTRUCTIONS"):
            payload = core_manifest(entry, env_root)

        assert payload["instructions"] == "5"
        assert "NO TASK INSTRUCTIONS" in caplog.text
        assert "falling back to the entry name '5'" in caplog.text.lower()
        assert "without a real task description" in caplog.text.lower()

    def test_empty_metadata_falls_through_to_file(self, tmp_path: Path) -> None:
        env_root = tmp_path / "env"
        env_root.mkdir()
        (env_root / "instruction.md").write_text("real task prose from disk", encoding="utf-8")
        entry = _entry(metadata={"instructions": "  ", "notes": "blank on purpose"})

        payload = core_manifest(entry, env_root)

        assert payload["instructions"] == "real task prose from disk"

    def test_example_manifest_keeps_inline_metadata_behavior(self) -> None:
        registry = CorpusRegistry()
        examples = [e for e in registry.all() if e.name == "Example Terminal Environment"]
        assert examples, "manifests/example.yaml should still load"
        entry = examples[0]
        assert "instructions" not in entry.metadata

        with pytest.warns(MissingInstructionsWarning, match="NO TASK INSTRUCTIONS"):
            payload = core_manifest(entry)

        assert payload["instructions"] == entry.name
        assert payload["format"] == "terminal"
        assert payload["domain"] == "tool_use"

    def test_inline_metadata_without_env_root_is_unchanged(self) -> None:
        entry = _entry(metadata={"instructions": "do the task"})
        payload = core_manifest(entry)
        assert payload["instructions"] == "do the task"


def _tw_task5() -> ManifestEntry:
    registry = CorpusRegistry()
    matches = [
        entry
        for entry in registry.all()
        if entry.name == "5"
        and entry.source.url == "https://github.com/few-sh/terminal-wrench"
    ]
    assert matches, "manifests/terminal_wrench.yaml must still contain task 5"
    return matches[0]


def _git(args: list[str], *, cwd: Path) -> None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        pytest.fail(f"git {' '.join(args)} failed: {detail}")


def _sparse_checkout_entry(entry: ManifestEntry, clone_dir: Path) -> Path:
    """Fetch the pinned commit (sparse to this task) into ``clone_dir``.

    The upstream repo is ~1.2GB with trajectories; cloning it whole in CI
    would not test anything extra about instructions. The objects we read
    are still the real pin.
    """
    assert entry.source.url is not None
    assert entry.source.commit is not None
    clone_dir.mkdir(parents=True)
    _git(["init"], cwd=clone_dir)
    _git(["remote", "add", "origin", entry.source.url], cwd=clone_dir)
    _git(["sparse-checkout", "init", "--cone"], cwd=clone_dir)
    _git(["sparse-checkout", "set", entry.source.path], cwd=clone_dir)
    _git(["fetch", "--depth", "1", "origin", entry.source.commit], cwd=clone_dir)
    _git(["checkout", "FETCH_HEAD"], cwd=clone_dir)
    env_root = clone_dir / entry.source.path
    if not env_root.is_dir():
        pytest.fail(f"sparse checkout missing {entry.source.path} in {clone_dir}")
    return env_root


@pytest.mark.integration
class TestLiveTerminalWrenchInstructions:
    def test_task_5_resolve_reads_instruction_md(self, tmp_path: Path) -> None:
        entry = _tw_task5()
        assert "instructions" not in entry.metadata

        cache_dir = tmp_path / "cache"
        clone_dir = repo_cache_dir(entry, cache_dir)
        env_root = _sparse_checkout_entry(entry, clone_dir)
        (clone_dir / COMMIT_MARKER).write_text(entry.source.commit + "\n", encoding="utf-8")
        assert is_fetched(entry, cache_dir=cache_dir)

        payload = core_manifest(entry, env_root)
        instructions = payload["instructions"]
        print("\n===== live Terminal Wrench task 5 instructions =====")
        print(instructions)
        print("===== end =====\n")

        assert instructions != entry.name
        assert instructions != "5"
        assert len(instructions) > 80
        assert "redirect" in instructions.lower()
        assert "output1.txt" in instructions
        assert "combined.txt" in instructions

        captured: dict = {}

        def _capture(spec: dict) -> dict:
            captured["spec"] = spec
            return spec

        with patch("verity_core.adapters.load_env", side_effect=_capture):
            resolved = resolve(entry, cache_dir=cache_dir)
        assert resolved is captured["spec"]
        assert captured["spec"]["instructions"] == instructions
