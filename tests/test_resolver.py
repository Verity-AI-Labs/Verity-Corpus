"""Tests for core_manifest instruction resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from verity_corpus.models.manifest import DomainTag, ManifestEntry, SourceSpec
from verity_corpus.registry import CorpusRegistry
from verity_corpus.resolver import MissingInstructionsWarning, core_manifest


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
