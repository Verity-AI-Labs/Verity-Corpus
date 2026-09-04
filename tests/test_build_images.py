"""Tests for scripts/build_images.py (dry-run / layout helpers, no Docker daemon)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_build():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_images.py"
    spec = importlib.util.spec_from_file_location("build_images", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _task(repo: Path, task_id: str, *, dockerfile: str, tests: dict[str, str] | None = None) -> Path:
    env = repo / "tasks" / task_id / "claude-opus-4.6" / "original_task" / "environment"
    env.mkdir(parents=True)
    (env / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    if tests:
        tests_dir = env.parent / "tests"
        tests_dir.mkdir()
        for name, body in tests.items():
            (tests_dir / name).write_text(body, encoding="utf-8")
    return env / "Dockerfile"


def test_discover_and_dry_run_prints_environment_then_grading_layer(
    tmp_path: Path, capsys
) -> None:
    build = _load_build()
    repo = tmp_path / "tw"
    _task(
        repo,
        "5",
        dockerfile="FROM ubuntu:24.04\nWORKDIR /app\n",
        tests={"test.sh": "uv run pytest /tests/test_outputs.py\n"},
    )
    _task(
        repo,
        "schemelike",
        dockerfile="FROM python:3.13\nCOPY tests/interp.py /app\n",
        tests={"test.sh": "true\n", "interp.py": "print(1)\n"},
    )
    # Self-contained COPY of tests into /app still lives in environment/; grading
    # tests remain at original_task/tests and still need the /tests layer.
    env_tests = repo / "tasks" / "schemelike" / "claude-opus-4.6" / "original_task" / "environment" / "tests"
    env_tests.mkdir()
    (env_tests / "interp.py").write_text("print(1)\n", encoding="utf-8")

    assert build.main(["--repo-root", str(repo), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "docker build -t verity-tw:5" in out
    assert "grading layer for verity-tw:5" in out
    assert "COPY tests /tests" in out
    assert "verity-tw:5" in out
    assert "grading layer for verity-tw:schemelike" in out
    assert "verity-tw:schemelike" in out


def test_dry_run_task_filter(tmp_path: Path, capsys) -> None:
    build = _load_build()
    repo = tmp_path / "tw"
    _task(repo, "5", dockerfile="FROM ubuntu:24.04\n", tests={"test.sh": "true\n"})
    _task(repo, "8", dockerfile="FROM ubuntu:24.04\n", tests={"test.sh": "true\n"})
    assert build.main(["--repo-root", str(repo), "--dry-run", "--task", "5"]) == 0
    out = capsys.readouterr().out
    assert "verity-tw:5" in out
    assert "verity-tw:8" not in out


def test_dockerfile_copies_tests_ignores_heredocs() -> None:
    build = _load_build()
    assert build.dockerfile_copies_tests.__doc__
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "Dockerfile"
        path.write_text(
            "FROM ubuntu\nCOPY --chmod=755 <<'EOF' /usr/local/bin/losetup\ntrue\nEOF\n",
            encoding="utf-8",
        )
        assert build.dockerfile_copies_tests(path) is False
        path.write_text("FROM ubuntu\nCOPY tests/interp.py /app\n", encoding="utf-8")
        assert build.dockerfile_copies_tests(path) is True


def test_grading_layer_skips_when_no_tests_dir(tmp_path: Path, capsys) -> None:
    build = _load_build()
    repo = tmp_path / "tw"
    _task(repo, "empty", dockerfile="FROM ubuntu:24.04\nWORKDIR /app\n")
    assert build.main(["--repo-root", str(repo), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "docker build -t verity-tw:empty" in out
    assert "grading layer" not in out
