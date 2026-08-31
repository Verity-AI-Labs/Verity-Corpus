"""Fetch environment sources into the local cache.

Git sources are cloned under ``cache/{entry.id}`` and never committed. Local
sources are used in place. Callers own the decision to fetch; this module
does not reach into the registry.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from verity_corpus import config
from verity_corpus.models.manifest import ManifestEntry

__all__ = ["FetchError", "cached_root", "fetch", "is_fetched"]

COMMIT_MARKER = ".verity_commit"


class FetchError(RuntimeError):
    """Raised when a source cannot be fetched or a local path is missing."""


def _run_git(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise FetchError(f"git {' '.join(args)} failed: {detail}")
    return result


def cached_root(entry: ManifestEntry, cache_dir: Path) -> Path:
    """Return the on-disk environment root for ``entry`` (does not fetch)."""
    if entry.source.type == "local":
        return Path(entry.source.path)
    return Path(cache_dir) / entry.id / entry.source.path


def _marker_path(target_dir: Path) -> Path:
    return target_dir / COMMIT_MARKER


def _recorded_commit(target_dir: Path) -> str | None:
    marker = _marker_path(target_dir)
    if not marker.is_file():
        return None
    return marker.read_text(encoding="utf-8").strip() or None


def is_fetched(entry: ManifestEntry, cache_dir: Path = config.CACHE_DIR) -> bool:
    """True when the cache holds this entry at the pinned commit (or the local path exists)."""
    if entry.source.type == "local":
        return Path(entry.source.path).exists()

    target_dir = Path(cache_dir) / entry.id
    recorded = _recorded_commit(target_dir)
    if recorded is None:
        return False
    if entry.source.commit is None:
        return True
    return recorded == entry.source.commit or recorded.startswith(entry.source.commit)


def fetch(entry: ManifestEntry, cache_dir: Path = config.CACHE_DIR) -> Path:
    """Materialize ``entry`` and return the environment root path."""
    if entry.source.type == "local":
        root = Path(entry.source.path)
        if not root.exists():
            raise FetchError(f"local source path does not exist: {root}")
        return root

    if entry.source.type != "git":
        raise FetchError(f"unsupported source type {entry.source.type!r}")
    if not entry.source.url:
        raise FetchError(f"git source for {entry.id} is missing a url")

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target_dir = cache_dir / entry.id

    if is_fetched(entry, cache_dir=cache_dir) and target_dir.is_dir():
        return (target_dir / entry.source.path).resolve()

    if target_dir.exists():
        shutil.rmtree(target_dir)

    _run_git(["clone", "--depth", "1", entry.source.url, str(target_dir)])

    if entry.source.commit:
        try:
            _run_git(["fetch", "--depth", "1", "origin", entry.source.commit], cwd=target_dir)
        except FetchError:
            # Some hosts refuse fetching a raw hash from a shallow clone; retry unshallow.
            _run_git(["fetch", "--unshallow", "origin"], cwd=target_dir)
            _run_git(["fetch", "origin", entry.source.commit], cwd=target_dir)
        _run_git(["checkout", entry.source.commit], cwd=target_dir)

    rev = _run_git(["rev-parse", "HEAD"], cwd=target_dir)
    _marker_path(target_dir).write_text(rev.stdout.strip() + "\n", encoding="utf-8")

    env_root = target_dir / entry.source.path
    if not env_root.exists():
        raise FetchError(
            f"cloned {entry.source.url} but path {entry.source.path!r} does not exist in {target_dir}"
        )
    return env_root.resolve()
