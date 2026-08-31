"""Bridge corpus manifest entries to verity-core adapters.

This is the only coupling point between verity-corpus and verity-core.
Fetching is the caller's job: resolving an unfetched git entry raises
rather than cloning as a side effect.

Core adapters are looked up by ``entry.adapter`` through
:func:`verity_core.adapters.canonical_format` / :func:`verity_core.adapters.load_env`.
The adapter is constructed from a mapping that Core already understands
(``format``, ``id``, ``domain``, plus ``adapter_config`` fields).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from verity_core.adapters import load_env
from verity_core.env import VerityEnv

from verity_corpus import config
from verity_corpus.fetcher import cached_root, is_fetched
from verity_corpus.models.manifest import ManifestEntry

__all__ = ["ResolveError", "core_manifest", "resolve"]

# Corpus domain tags are broader than Core's TaskSpec.Domain. Map the ones that
# do not exist on Core onto the closest Core domain so adapters can parse.
_DOMAIN_TO_CORE: dict[str, str] = {
    "terminal": "tool_use",
    "browser": "browser",
    "gui": "gui",
    "code": "code",
    "api": "other",
    "math": "math",
    "other": "other",
}


class ResolveError(RuntimeError):
    """Raised when an entry cannot be turned into a :class:`VerityEnv`."""


def core_manifest(entry: ManifestEntry, env_root: Path) -> dict[str, Any]:
    """Project a corpus entry into the mapping Core's ``load_env`` expects."""
    payload: dict[str, Any] = {
        "id": entry.id,
        "format": entry.adapter,
        "domain": _DOMAIN_TO_CORE.get(entry.domain.category, "other"),
        "source": entry.source.url or str(env_root),
        "commit": entry.source.commit or "",
        "instructions": entry.metadata.get("instructions", entry.name),
        "env_root": str(env_root),
        **entry.adapter_config,
    }
    return payload


def resolve(entry: ManifestEntry, cache_dir: Path = config.CACHE_DIR) -> VerityEnv:
    """Instantiate the Core adapter named by ``entry.adapter``.

    Does not fetch. Call :func:`verity_corpus.fetcher.fetch` first.
    """
    if not is_fetched(entry, cache_dir=cache_dir):
        raise ResolveError(
            f"environment {entry.id} ({entry.name}) is not fetched; "
            "call fetch() before resolve()"
        )
    env_root = cached_root(entry, cache_dir)
    try:
        return load_env(core_manifest(entry, env_root))
    except Exception as exc:  # noqa: BLE001 — Core raises ManifestError; keep the bridge thin
        raise ResolveError(
            f"failed to resolve {entry.id} via adapter {entry.adapter!r}: {exc}"
        ) from exc
