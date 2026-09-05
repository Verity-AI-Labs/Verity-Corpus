"""Bridge corpus manifest entries to verity-core adapters.

This is the only coupling point between verity-corpus and verity-core.
Fetching is the caller's job: resolving an unfetched git entry raises
rather than cloning as a side effect.

verity-core is imported inside :func:`resolve` so the rest of Corpus
(registry, fetcher, models) can load when Core is not installed.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

from verity_corpus import config
from verity_corpus.fetcher import cached_root, is_fetched
from verity_corpus.hack_trajectories import (
    HackTrajectoryPresence,
    HackTrajectorySet,
    has_recorded_hacks,
    load_from_env_root,
)
from verity_corpus.models.manifest import ManifestEntry

__all__ = [
    "DOMAIN_TO_CORE",
    "IMAGE_REQUIRED_ADAPTERS",
    "INSTRUCTION_FILENAMES",
    "MissingInstructionsWarning",
    "ResolveError",
    "core_manifest",
    "inventory_hack_trajectories",
    "load_hack_trajectories",
    "load_hack_trajectories_for_task",
    "resolve",
]

logger = logging.getLogger(__name__)

# First existing non-empty file in env_root wins. Terminal Wrench (and
# Terminal-Bench) ship ``instruction.md`` at the task root; other git-sourced
# benchmarks may use one of the aliases. Presence on disk, not source name,
# decides whether we read a file.
INSTRUCTION_FILENAMES: tuple[str, ...] = (
    "instruction.md",
    "instructions.md",
    "INSTRUCTION.md",
    "INSTRUCTIONS.md",
    "instruction.txt",
    "instructions.txt",
)

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


class MissingInstructionsWarning(UserWarning):
    """Emitted when ``core_manifest`` falls back to the entry name for instructions."""


def _metadata_instructions(entry: ManifestEntry) -> str | None:
    raw = entry.metadata.get("instructions")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _file_instructions(env_root: Path) -> str | None:
    for name in INSTRUCTION_FILENAMES:
        path = env_root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if text:
            return text
    return None


def _resolve_instructions(entry: ManifestEntry, env_root: Path | None) -> str:
    """Return task instructions: metadata, then a file in ``env_root``, then name."""
    from_meta = _metadata_instructions(entry)
    if from_meta is not None:
        return from_meta
    if env_root is not None:
        from_file = _file_instructions(env_root)
        if from_file is not None:
            return from_file
    root_label = str(env_root) if env_root is not None else "<no env_root>"
    message = (
        f"NO TASK INSTRUCTIONS for environment {entry.id} ({entry.name}): "
        f"metadata has no 'instructions' and no instruction file "
        f"{INSTRUCTION_FILENAMES} was found under {root_label}. "
        f"Falling back to the entry name {entry.name!r}. "
        "Audits of this environment will run without a real task description."
    )
    logger.warning(message)
    warnings.warn(message, MissingInstructionsWarning, stacklevel=3)
    return entry.name


def core_manifest(entry: ManifestEntry, env_root: Path | None = None) -> dict[str, Any]:
    """Project a corpus entry into the mapping Core's ``load_env`` expects.

    ``instructions`` prefer an explicit ``metadata['instructions']`` string,
    then a task-instructions file in ``env_root`` (``instruction.md`` and
    similar), then ``entry.name`` with a loud warning.

    Container adapters (``terminal``, ``docker_test``) require ``adapter_config``
    to include an ``image`` field. That is flattened onto this mapping so Core
    sees it as a top-level key.
    """
    payload: dict[str, Any] = {
        "id": entry.id,
        "format": entry.adapter,
        "domain": DOMAIN_TO_CORE.get(entry.domain.category, "other"),
        "source": entry.source.url
        or (str(env_root) if env_root is not None else entry.source.path),
        "commit": entry.source.commit or "",
        "instructions": _resolve_instructions(entry, env_root),
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


def load_hack_trajectories(
    entry: ManifestEntry,
    cache_dir: Path = config.CACHE_DIR,
    env_root: Path | None = None,
) -> HackTrajectorySet:
    """Read recorded exploits for ``entry`` from its fetched ``env_root``.

    Same root :func:`resolve` uses (``cached_root``). Does not fetch. Walks
    every ``<model>/hack_trajectories/<version>/`` tree under the task dir.
    A missing tree — the current sparse-checkout state — returns an empty set
    with a diagnostic ``no hack trajectories found for task X: …``.
    """
    root = Path(env_root) if env_root is not None else cached_root(entry, cache_dir)
    return load_from_env_root(root, task_id=entry.name, env_id=entry.id)


def load_hack_trajectories_for_task(
    task_id: str,
    registry: Any | None = None,
    cache_dir: Path = config.CACHE_DIR,
) -> HackTrajectorySet:
    """Look up ``task_id`` on the registry (id or name) and load its exploits."""
    from verity_corpus.registry import CorpusRegistry

    index = registry if registry is not None else CorpusRegistry()
    entry = index.by_task_id(task_id)
    if entry is None:
        raise ResolveError(f"unknown task id {task_id!r}")
    return load_hack_trajectories(entry, cache_dir=cache_dir)


def inventory_hack_trajectories(
    registry: Any | None = None,
    cache_dir: Path = config.CACHE_DIR,
) -> list[HackTrajectoryPresence]:
    """Flag each non-catalog registry entry as having recorded exploits or not.

    Counts version dirs whose ``metadata.json`` labels them as actual hacks
    (not legitimate solves). Cheap enough to run across the 331 Terminal
    Wrench tasks: metadata only, not the full ``trajectory.json``.
    """
    from verity_corpus.registry import CorpusRegistry

    index = registry if registry is not None else CorpusRegistry()
    rows: list[HackTrajectoryPresence] = []
    for entry in index.all():
        if entry.status == "catalog":
            continue
        env_root = cached_root(entry, cache_dir)
        present, count, hack_root = has_recorded_hacks(env_root)
        if present:
            message = ""
        else:
            message = load_from_env_root(
                env_root, task_id=entry.name, env_id=entry.id
            ).message
        rows.append(
            HackTrajectoryPresence(
                task_id=entry.name,
                env_id=entry.id,
                present=present,
                n_trajectories=count,
                message=message,
                hack_root=str(hack_root) if hack_root is not None else "",
            )
        )
    return rows


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
