"""Tests for scripts/gen_terminal_wrench.py against a tiny fake checkout."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml


def _load_gen():
    path = Path(__file__).resolve().parents[1] / "scripts" / "gen_terminal_wrench.py"
    spec = importlib.util.spec_from_file_location("gen_terminal_wrench", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_entries_reads_tasks_json_and_task_toml(tmp_path: Path) -> None:
    gen = _load_gen()
    repo = tmp_path / "tw"
    task_dir = repo / "tasks" / "cobol-modernization" / "claude-opus-4.6" / "original_task"
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        """\
[metadata]
difficulty = "easy"
category = "software-engineering"
[verifier]
timeout_sec = 180
""",
        encoding="utf-8",
    )
    (repo / "index").mkdir()
    (repo / "index" / "tasks.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "cobol-modernization",
                    "model": "claude-opus-4.6",
                    "source_dataset": "TerminalBench-original",
                    "source_datasets": ["TerminalBench-original"],
                },
                {
                    "task_id": "104",
                    "model": "gemini-3.1-pro",
                    "source_dataset": "seta_2026_01_29",
                    "source_datasets": ["seta_2026_01_29"],
                },
            ]
        ),
        encoding="utf-8",
    )
    (repo / "task_source_datasets.json").write_text(
        json.dumps({"cobol-modernization": ["TerminalBench-original"], "104": ["seta_2026_01_29"]}),
        encoding="utf-8",
    )

    entries = gen.collect_entries(repo)
    assert [e["name"] for e in entries] == ["104", "cobol-modernization"]
    named = entries[1]
    assert named["path"] == "tasks/cobol-modernization/claude-opus-4.6/original_task"
    assert named["adapter_config"]["image"] == "verity-tw:cobol-modernization"
    assert named["adapter_config"]["timeout"] == 180
    assert named["metadata"]["difficulty"] == "easy"
    assert named["metadata"]["dockerfile"] == "environment/Dockerfile"
    numeric = entries[0]
    assert numeric["adapter_config"]["image"] == "verity-tw:104"
    assert numeric["metadata"]["upstream_task_id"] == "104"
    assert "difficulty" not in numeric["metadata"]

    rendered = gen.render_manifest(entries)
    assert "scripts/gen_terminal_wrench.py" in rendered
    parsed = yaml.safe_load(rendered)
    assert parsed["source_defaults"]["commit"] == gen.TW_COMMIT
    assert len(parsed["entries"]) == 2
