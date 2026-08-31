#!/usr/bin/env python3
"""Build ``verity-tw:{task_id}`` images from Terminal Wrench Dockerfiles.

Core's ``ContainerEnv`` cannot build from a Dockerfile yet (TODO in
``verity_core.adapters.base.ContainerEnv._resolve_image``). It requires a
prebuilt ``image`` string. This helper is run by hand after fetching the
Terminal Wrench clone; it is not part of the ``verity_corpus`` library.

The tag for each task is ``verity-tw:{task_id}``, matching
``adapter_config.image`` in ``manifests/terminal_wrench.yaml``.

    python scripts/build_images.py --repo-root /path/to/terminal-wrench
    python scripts/build_images.py --repo-root /path/to/terminal-wrench --dry-run

The Docker build context is the task's ``environment/`` directory (where the
Dockerfile lives). Does not pull or push images.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MODEL_DIR = "claude-opus-4.6"
DOCKERFILE_REL = Path(MODEL_DIR) / "original_task" / "environment" / "Dockerfile"


def image_tag(task_id: str) -> str:
    """Return the deterministic image tag for a Terminal Wrench task."""
    return f"verity-tw:{task_id}"


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


def build_image(task_id: str, dockerfile: Path, *, dry_run: bool) -> str:
    tag = image_tag(task_id)
    context = dockerfile.parent
    command = ["docker", "build", "-t", tag, "-f", str(dockerfile), str(context)]
    if dry_run:
        print(f"dry-run: {' '.join(command)}")
        print(tag)
        return tag
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise SystemExit(f"docker build failed for {task_id} (tag {tag})")
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
    if not pairs:
        print(f"no Dockerfiles found under {repo_root / 'tasks'}", file=sys.stderr)
        return 1
    for task_id, dockerfile in pairs:
        build_image(task_id, dockerfile, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
