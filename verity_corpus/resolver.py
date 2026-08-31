"""Bridge corpus manifest entries to verity-core adapters.

This is the only coupling point between verity-corpus and verity-core.
Fetching is the caller's job: resolving an unfetched git entry raises
rather than cloning as a side effect.

verity-core is imported inside :func:`resolve` so the rest of Corpus
(registry, fetcher, models) can load when Core is not installed.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from verity_corpus import config
from verity_corpus.fetcher import cached_root, is_fetched
from verity_corpus.models.manifest import ManifestEntry

__all__ = [
    "DOMAIN_TO_CORE",
    "IMAGE_REQUIRED_ADAPTERS",
    "ResolveError",
    "core_manifest",
    "resolve",
]

# Corpus domain tags are broader than Core's TaskSpec.Domain. Map the ones that
# do not exist on Core onto the closest Core domain so adapters can parse.
DOMAIN_TO_CORE: dict[str, str] = {
    "terminal": "tool_use",
    "browser": "browser",
    "gui": "gui",
    "code": "code",
    "api": "other",
    "math": "math",
    "other": "other",
}

# Core's ContainerEnv.__init__ requires a prebuilt `image`. TerminalAdapter and
# DockerTestAdapter both inherit that check. adapter_config must supply it or
# load_env will raise ManifestError at construction time.
IMAGE_REQUIRED_ADAPTERS = frozenset({"terminal", "docker_test"})

_CORE_MISSING_MSG = (
    "verity-core is required to resolve environments. "
    "Install it from https://github.com/Verity-AI-Labs/Verity-Core"
)


class ResolveError(RuntimeError):
    """Raised when an entry cannot be turned into a VerityEnv."""


def core_manifest(entry: ManifestEntry, env_root: Path | None = None) -> dict[str, Any]:
    """Project a corpus entry into the mapping Core's ``load_env`` expects.

    Container adapters (``terminal``, ``docker_test``) require ``adapter_config``
    to include an ``image`` field. That is flattened onto this mapping so Core
    sees it as a top-level key.
    """
    payload: dict[str, Any] = {
        "id": entry.id,
        "format": entry.adapter,
        "domain": DOMAIN_TO_CORE.get(entry.domain.category, "other"),
        "source": entry.source.url or (str(env_root) if env_root is not None else entry.source.path),
        "commit": entry.source.commit or "",
        "instructions": entry.metadata.get("instructions", entry.name),
        **entry.adapter_config,
    }
    if env_root is not None:
        payload["env_root"] = str(env_root)
    return payload


def _warn_if_image_missing(entry: ManifestEntry) -> None:
    if entry.adapter in IMAGE_REQUIRED_ADAPTERS and "image" not in entry.adapter_config:
        warnings.warn(
            f"adapter {entry.adapter!r} for {entry.id} ({entry.name}) has no "
            f"'image' in adapter_config; Core's ContainerEnv will raise ManifestError "
            f"until one is supplied",
            UserWarning,
            stacklevel=3,
        )


def resolve(entry: ManifestEntry, cache_dir: Path = config.CACHE_DIR) -> Any:
    """Instantiate the Core adapter named by ``entry.adapter``.

    Does not fetch. Call :func:`verity_corpus.fetcher.fetch` first.
    """
    try:
        from verity_core.adapters import load_env
    except ImportError as exc:
        raise ResolveError(_CORE_MISSING_MSG) from exc

    if not is_fetched(entry, cache_dir=cache_dir):
        raise ResolveError(
            f"environment {entry.id} ({entry.name}) is not fetched; "
            "call fetch() before resolve()"
        )
    _warn_if_image_missing(entry)
    env_root = cached_root(entry, cache_dir)
    try:
        return load_env(core_manifest(entry, env_root))
    except Exception as exc:  # Core raises ManifestError; keep the bridge thin
        raise ResolveError(
            f"failed to resolve {entry.id} via adapter {entry.adapter!r}: {exc}"
        ) from exc
