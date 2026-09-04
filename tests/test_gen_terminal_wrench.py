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
    env = task_dir / "environment"
    env.mkdir()
    (env / "Dockerfile").write_text("FROM ubuntu:24.04\nWORKDIR /app\n", encoding="utf-8")
    tests = task_dir / "tests"
    tests.mkdir()
    (tests / "test.sh").write_text("uv run pytest /tests/test_outputs.py -rA\n", encoding="utf-8")
    (tests / "test_outputs.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    workspace_task = repo / "tasks" / "891" / "claude-opus-4.6" / "original_task"
    workspace_task.mkdir(parents=True)
    (workspace_task / "environment").mkdir()
    (workspace_task / "environment" / "Dockerfile").write_text(
        "FROM ubuntu:22.04\nWORKDIR /workspace\n",
        encoding="utf-8",
    )
    (workspace_task / "tests").mkdir()
    (workspace_task / "tests" / "test.sh").write_text("true\n", encoding="utf-8")
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
                {
                    "task_id": "891",
                    "model": "claude-opus-4.6",
                    "source_dataset": "seta_2026_01_29",
                    "source_datasets": ["seta_2026_01_29"],
                },
            ]
        ),
        encoding="utf-8",
    )
    (repo / "task_source_datasets.json").write_text(
        json.dumps({"cobol-modernization": ["TerminalBench-original"], "104": ["seta_2026_01_29"], "891": ["seta_2026_01_29"]}),
        encoding="utf-8",
    )

    entries = gen.collect_entries(repo)
    assert [e["name"] for e in entries] == ["104", "891", "cobol-modernization"]
    named = entries[2]
    assert named["path"] == "tasks/cobol-modernization/claude-opus-4.6/original_task"
    assert named["adapter_config"]["image"] == "verity-tw:cobol-modernization"
    assert named["adapter_config"]["timeout"] == 180
    assert named["adapter_config"]["submission_path"] == "/app/solve.sh"
    assert named["adapter_config"]["apply_command"] == "cd /app && bash /app/solve.sh"
    assert named["adapter_config"]["test_command"] == (
        "cd /app && bash /tests/test.sh; _verity_s=$?; "
        "if [ -f /logs/verifier/reward.txt ]; then "
        "grep -qx 1 /logs/verifier/reward.txt; "
        "else exit $_verity_s; fi"
    )
    assert named["adapter_config"]["limits"] == {"network_disabled": False}
    assert named["metadata"]["difficulty"] == "easy"
    assert named["metadata"]["dockerfile"] == "environment/Dockerfile"
    numeric = entries[0]
    assert numeric["adapter_config"]["image"] == "verity-tw:104"
    assert numeric["metadata"]["upstream_task_id"] == "104"
    assert "difficulty" not in numeric["metadata"]
    assert "submission_path" not in numeric["adapter_config"]
    workspace = entries[1]
    assert workspace["adapter_config"]["submission_path"] == "/workspace/solve.sh"
    assert "apply_command" not in workspace["adapter_config"]
    assert workspace["adapter_config"]["test_command"] == (
        "bash /tests/test.sh; _verity_s=$?; "
        "if [ -f /logs/verifier/reward.txt ]; then "
        "grep -qx 1 /logs/verifier/reward.txt; "
        "else exit $_verity_s; fi"
    )

    rendered = gen.render_manifest(entries)
    assert "scripts/gen_terminal_wrench.py" in rendered
    parsed = yaml.safe_load(rendered)
    assert parsed["source_defaults"]["commit"] == gen.TW_COMMIT
    assert len(parsed["entries"]) == 3


def test_dockerfile_workdir_uses_last_instruction(tmp_path: Path) -> None:
    gen = _load_gen()
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu\nWORKDIR /first\n# WORKDIR /commented\nWORKDIR /app\n",
        encoding="utf-8",
    )
    assert gen.dockerfile_workdir(dockerfile) == "/app"
    assert gen.dockerfile_workdir(tmp_path / "missing") == "/app"


def test_infer_test_command_prefers_test_sh(tmp_path: Path) -> None:
    gen = _load_gen()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_outputs.py").write_text("x = 1\n", encoding="utf-8")
    (tests / "run-tests.sh").write_text("true\n", encoding="utf-8")
    (tests / "test.sh").write_text("true\n", encoding="utf-8")
    assert gen.infer_test_command(tests) == "bash /tests/test.sh"
    (tests / "test.sh").unlink()
    assert gen.infer_test_command(tests) == "bash /tests/run-tests.sh"
    (tests / "run-tests.sh").unlink()
    assert gen.infer_test_command(tests) == "python3 -m pytest /tests/test_outputs.py -rA"

