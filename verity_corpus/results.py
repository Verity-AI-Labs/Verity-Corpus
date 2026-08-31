"""Pipe Core tool output back into Corpus scorecard and VRC storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from verity_core.scorecard import Scorecard

from verity_corpus.models.vrc import VRCEntry
from verity_corpus.registry import CorpusRegistry
from verity_corpus.scorecard_store import exists, list_scored, load, save

__all__ = [
    "record_vrc_entry",
    "sync_status",
    "update_scorecard_from_core",
]


def update_scorecard_from_core(
    env_id: str,
    core_scorecard: Scorecard,
    scorecards_dir: Path,
) -> Scorecard:
    """Merge ``core_scorecard`` into the on-disk card for ``env_id``.

    Scored axes in the incoming card overwrite the same axes on disk.
    Axes the incoming card leaves unscored keep their previous values.
    """
    scorecards_dir = Path(scorecards_dir)
    if not exists(env_id, scorecards_dir):
        save(core_scorecard, scorecards_dir)
        return core_scorecard

    existing = load(env_id, scorecards_dir)
    for axis, incoming in core_scorecard.axes.items():
        if incoming.scored:
            existing.set_axis(
                axis,
                incoming.value,
                incoming.tool,
                incoming.evidence,
                incoming.notes,
            )
    existing.metadata.update(core_scorecard.metadata)
    save(existing, scorecards_dir)
    return existing


def record_vrc_entry(
    env_id: str,
    exploit_type: str,
    trajectory: list[dict[str, Any]],
    model_id: str,
    hackability_curve: dict[int, float] | None,
    vrc_dir: Path,
    notes: str = "",
) -> VRCEntry:
    """Create a VRC entry and write it to ``{vrc_dir}/{env_id}/{id}.json``."""
    entry = VRCEntry(
        env_id=env_id,
        exploit_type=exploit_type,
        trajectory=trajectory,
        model_id=model_id,
        hackability_curve=hackability_curve,
        notes=notes,
    )
    entry.save(Path(vrc_dir))
    return entry


def sync_status(registry: CorpusRegistry, scorecards_dir: Path) -> dict[str, str]:
    """Mark registry entries audited when a scorecard exists on disk.

    Status is in-memory only (manifest YAML is not rewritten). Returns
    ``env_id → new_status`` for entries that changed.
    """
    changed: dict[str, str] = {}
    for env_id in list_scored(Path(scorecards_dir)):
        entry = registry.by_id(env_id)
        if entry is None or entry.status == "audited":
            continue
        registry.update_status(env_id, "audited")
        changed[env_id] = "audited"
    return changed
