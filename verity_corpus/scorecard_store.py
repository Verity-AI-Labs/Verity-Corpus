"""Persist Core :class:`~verity_core.scorecard.Scorecard` objects under ``scorecards/``.

Corpus does not define its own scorecard model. Core's dataclass is canonical —
it is what the audit tools write — so this module only knows the directory layout
and lookup-by-env-id. Paths use Core's :func:`scorecard_path` so a file written
here is the same file Core's batch runner would find.
"""

from __future__ import annotations

from pathlib import Path

from verity_core.scorecard import Scorecard, scorecard_path

from verity_corpus import config

__all__ = ["exists", "list_scored", "load", "save"]


def save(scorecard: Scorecard, directory: Path | None = None) -> Path:
    """Write ``scorecard`` to ``{directory}/{slug(env_id)}.json`` via Core's serializer."""
    directory = Path(directory) if directory is not None else config.SCORECARDS_DIR
    path = scorecard_path(directory, scorecard.env_id)
    scorecard.to_json(path)
    return path


def load(env_id: str, directory: Path | None = None) -> Scorecard:
    """Read the scorecard for ``env_id`` from ``directory``."""
    directory = Path(directory) if directory is not None else config.SCORECARDS_DIR
    path = scorecard_path(directory, env_id)
    if not path.is_file():
        raise FileNotFoundError(f"no scorecard for {env_id!r} at {path}")
    return Scorecard.from_json(path)


def exists(env_id: str, directory: Path | None = None) -> bool:
    """True when a scorecard file for ``env_id`` is already on disk."""
    directory = Path(directory) if directory is not None else config.SCORECARDS_DIR
    return scorecard_path(directory, env_id).is_file()


def list_scored(directory: Path | None = None) -> list[str]:
    """Return env ids that have a scorecard JSON file under ``directory``."""
    directory = Path(directory) if directory is not None else config.SCORECARDS_DIR
    if not directory.is_dir():
        return []
    ids: list[str] = []
    for path in sorted(directory.glob("*.json")):
        ids.append(Scorecard.from_json(path).env_id)
    return ids
