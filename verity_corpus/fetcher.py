"""Fetch environment sources into the local cache.

Git sources share a clone per ``(url, commit)`` under ``cache/repos/{hash}/``.
An entry's environment root is ``{shared_clone}/{entry.source.path}``, so many
manifest rows that pin the same revision (Terminal Wrench's 331 tasks, for
example) clone the repo once. Local sources are used in place. Callers own the
decision to fetch; this module does not reach into the registry.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from verity_corpus import config
from verity_corpus.models.manifest import ManifestEntry, SourceSpec

__all__ = [
    "COMMIT_MARKER",
    "FetchError",
    "cached_root",
    "fetch",
    "is_fetched",
    "repo_cache_dir",
    "repo_cache_key",
]

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


def repo_cache_key(url: str, commit: str | None) -> str:
    """Return a short stable hash of ``(url, commit-or-HEAD)`` for the shared clone dir."""
    payload = "\0".join((url, commit or "HEAD"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def repo_cache_dir(entry: ManifestEntry, cache_dir: Path) -> Path:
    """Directory of the shared shallow clone for ``entry``'s ``(url, commit)`` pin."""
    if not entry.source.url:
        raise FetchError(f"git source for {entry.id} is missing a url")
    return Path(cache_dir) / "repos" / repo_cache_key(entry.source.url, entry.source.commit)


def cached_root(entry: ManifestEntry, cache_dir: Path) -> Path:
    """Return the on-disk environment root for ``entry`` (does not fetch)."""
    if entry.source.type == "local":
        return Path(entry.source.path)
    return repo_cache_dir(entry, cache_dir) / entry.source.path


def _marker_path(target_dir: Path) -> Path:
    return target_dir / COMMIT_MARKER


def _recorded_commit(target_dir: Path) -> str | None:
    marker = _marker_path(target_dir)
    if not marker.is_file():
        return None
    return marker.read_text(encoding="utf-8").strip() or None


def _commit_matches(recorded: str, pinned: str) -> bool:
    return recorded == pinned or recorded.startswith(pinned) or pinned.startswith(recorded)


def is_fetched(entry: ManifestEntry, cache_dir: Path = config.CACHE_DIR) -> bool:
    """True when the shared clone holds this pin (or the local path exists)."""
    if entry.source.type == "local":
        return Path(entry.source.path).exists()

    try:
        target_dir = repo_cache_dir(entry, cache_dir)
    except FetchError:
        return False
    recorded = _recorded_commit(target_dir)
    if recorded is None:
        return False
    if entry.source.commit is None:
        return True
    return _commit_matches(recorded, entry.source.commit)


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
    target_dir = repo_cache_dir(entry, cache_dir)
    env_root = cached_root(entry, cache_dir)

    if is_fetched(entry, cache_dir=cache_dir) and target_dir.is_dir():
        if not env_root.exists():
            raise FetchError(
                f"cloned {entry.source.url} but path {entry.source.path!r} "
                f"does not exist in {target_dir}"
            )
        return env_root.resolve()

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        shutil.rmtree(target_dir)

    _clone_pinned(entry.source, target_dir)

    if not env_root.exists():
        raise FetchError(
            f"cloned {entry.source.url} but path {entry.source.path!r} does not exist in {target_dir}"
        )
    return env_root.resolve()


def _clone_pinned(source: SourceSpec, target_dir: Path) -> None:
    assert source.url is not None
    _run_git(["clone", "--depth", "1", source.url, str(target_dir)])

    if source.commit:
        try:
            _run_git(["fetch", "--depth", "1", "origin", source.commit], cwd=target_dir)
        except FetchError:
            # Some hosts refuse fetching a raw hash from a shallow clone; retry unshallow.
            _run_git(["fetch", "--unshallow", "origin"], cwd=target_dir)
            _run_git(["fetch", "origin", source.commit], cwd=target_dir)
        _run_git(["checkout", source.commit], cwd=target_dir)

    rev = _run_git(["rev-parse", "HEAD"], cwd=target_dir)
    _marker_path(target_dir).write_text(rev.stdout.strip() + "\n", encoding="utf-8")
