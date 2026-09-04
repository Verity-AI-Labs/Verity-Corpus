#!/usr/bin/env python3
"""Build ``verity-tw:{task_id}`` images from Terminal Wrench Dockerfiles.

Core's ``ContainerEnv`` cannot build from a Dockerfile yet (TODO in
``verity_core.adapters.base.ContainerEnv._resolve_image``). It requires a
prebuilt ``image`` string. This helper is run by hand after fetching the
Terminal Wrench clone; it is not part of the ``verity_corpus`` library.

The tag for each task is ``verity-tw:{task_id}``, matching
``adapter_config.image`` in ``manifests/terminal_wrench.yaml``.

    python scripts/build_images.py --repo-root /path/to/terminal-wrench
    python scripts/build_images.py --repo-root /path/to/terminal-wrench --task 5
    python scripts/build_images.py --repo-root /path/to/terminal-wrench --dry-run

The Docker build context is the task's ``environment/`` directory (where the
Dockerfile lives), so task-specific ``COPY data`` / ``COPY check.py`` /
``COPY tests/...`` instructions keep working.

Most Terminal Wrench tasks do **not** bake grading files into that image.
Across the already-built sample, ``tests/`` (and ``solution/``) sit beside
``environment/`` at ``original_task/``, and the original Terminal-Bench
harness bind-mounts ``tests/`` at ``/tests`` at grade time. Core's sandbox
has no volume API, so after the environment image builds this script adds a
grading layer that copies ``original_task/tests`` to ``/tests``. ``solution/``
is left out of the image on purpose: baking the gold answer would leak it to
the agent.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MODEL_DIR = "claude-opus-4.6"
DOCKERFILE_REL = Path(MODEL_DIR) / "original_task" / "environment" / "Dockerfile"

# COPY/ADD of a tests/ path. Inline COPY <<EOF is ignored (no source path).
_COPY_TESTS_RE = re.compile(
    r"^\s*(COPY|ADD)\s+(?!--from=)(?P<body>\S.*)$",
    re.IGNORECASE,
)


def image_tag(task_id: str) -> str:
    """Return the deterministic image tag for a Terminal Wrench task."""
    return f"verity-tw:{task_id}"


def original_task_dir(dockerfile: Path) -> Path:
    """Return ``original_task/`` given ``.../original_task/environment/Dockerfile``."""
    return dockerfile.parent.parent


def dockerfile_copies_tests(dockerfile: Path) -> bool:
    """True when the Dockerfile copies a ``tests/`` path from the build context.

    This is the minority pattern (e.g. schemelike-metacircular-eval copies
    helper tests into ``/app``). It does **not** mean grading tests land at
    ``/tests``; that still requires the grading layer.
    """
    try:
        text = dockerfile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _COPY_TESTS_RE.match(line)
        if match is None:
            continue
        body = match.group("body")
        if "<<" in body:
            continue
        tokens = body.split()
        if any(token == "tests" or token.startswith("tests/") for token in tokens[:-1]):
            return True
    return False


def grading_layer_dockerfile(base_tag: str) -> str:
    """Dockerfile that overlays ``original_task/tests`` at ``/tests``.

    ``/logs/verifier`` is created because stock ``test.sh`` writes
    ``/logs/verifier/reward.txt`` after pytest; a missing directory would
    fail an otherwise-correct run. ``/workspace`` exists so Core's default
    sandbox workdir is a real path even when the image ``WORKDIR`` is ``/app``.
    """
    return (
        f"FROM {base_tag}\n"
        "COPY tests /tests\n"
        "RUN mkdir -p /logs/verifier /workspace\n"
    )


def discover_dockerfiles(repo_root: Path) -> list[tuple[str, Path]]:
    """Return ``(task_id, dockerfile)`` pairs under ``repo_root/tasks``."""
    tasks_dir = repo_root / "tasks"
    if not tasks_dir.is_dir():
        raise SystemExit(f"no tasks/ directory under {repo_root}")
    found: list[tuple[str, Path]] = []
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        dockerfile = task_dir / DOCKERFILE_REL
        if dockerfile.is_file():
            found.append((task_dir.name, dockerfile))
    return found


def _run_docker(command: list[str], *, dry_run: bool) -> None:
    if dry_run:
        print(f"dry-run: {' '.join(command)}")
        return
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise SystemExit(f"docker build failed: {' '.join(command)}")


def apply_grading_layer(tag: str, tests_dir: Path, *, dry_run: bool) -> None:
    """Rebuild ``tag`` FROM itself, copying ``tests_dir`` to ``/tests``."""
    contents = grading_layer_dockerfile(tag)
    if dry_run:
        print(f"dry-run: grading layer for {tag} (COPY tests /tests)")
        print(contents.rstrip())
        return
    with tempfile.TemporaryDirectory(prefix="verity-tw-tests-") as tmp:
        staging = Path(tmp)
        shutil.copytree(tests_dir, staging / "tests")
        dockerfile = staging / "Dockerfile"
        dockerfile.write_text(contents, encoding="utf-8")
        _run_docker(
            ["docker", "build", "-t", tag, "-f", str(dockerfile), str(staging)],
            dry_run=False,
        )


def stage_environment_context(dockerfile: Path) -> tuple[Path, Path, Path | None]:
    """Return ``(dockerfile, context, cleanup_dir)`` for the environment build.

    If the Dockerfile ``COPY``s ``tests/`` but ``environment/tests`` is missing
    while ``original_task/tests`` exists, copy tests into a temporary context
    so that COPY succeeds without mutating the fetched clone.
    """
    env_dir = dockerfile.parent
    tests_dir = original_task_dir(dockerfile) / "tests"
    env_tests = env_dir / "tests"
    if dockerfile_copies_tests(dockerfile) and not env_tests.exists() and tests_dir.is_dir():
        tmp = Path(tempfile.mkdtemp(prefix="verity-tw-env-"))
        shutil.copytree(env_dir, tmp, dirs_exist_ok=True)
        shutil.copytree(tests_dir, tmp / "tests")
        return tmp / dockerfile.name, tmp, tmp
    return dockerfile, env_dir, None


def build_image(task_id: str, dockerfile: Path, *, dry_run: bool) -> str:
    tag = image_tag(task_id)
    df, context, cleanup = stage_environment_context(dockerfile)
    try:
        _run_docker(
            ["docker", "build", "-t", tag, "-f", str(df), str(context)],
            dry_run=dry_run,
        )
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)

    tests_dir = original_task_dir(dockerfile) / "tests"
    if tests_dir.is_dir():
        apply_grading_layer(tag, tests_dir, dry_run=dry_run)
    elif not dry_run:
        print(f"warning: no tests/ next to environment/ for {task_id}; image has no /tests", file=sys.stderr)
    print(tag)
    return tag


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="Fetched Terminal Wrench checkout (the shared clone root, not a task subdir).",
    )
    parser.add_argument(
        "--task",
        action="append",
        dest="tasks",
        default=None,
        help="Build only this task id (repeatable). Default: every discovered Dockerfile.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print docker build commands and tags without running Docker.",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve()
    if not repo_root.is_dir():
        print(f"repo root does not exist: {repo_root}", file=sys.stderr)
        return 1
    pairs = discover_dockerfiles(repo_root)
    if args.tasks:
        wanted = set(args.tasks)
        pairs = [(task_id, path) for task_id, path in pairs if task_id in wanted]
        missing = wanted - {task_id for task_id, _ in pairs}
        if missing:
            print(f"no Dockerfile for task(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 1
    if not pairs:
        print(f"no Dockerfiles found under {repo_root / 'tasks'}", file=sys.stderr)
        return 1
    for task_id, dockerfile in pairs:
        build_image(task_id, dockerfile, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
