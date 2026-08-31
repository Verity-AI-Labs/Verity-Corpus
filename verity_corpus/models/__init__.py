"""Pydantic data models for manifests and VRC entries.

Scorecards are Core's :class:`~verity_core.scorecard.Scorecard`; they are not
re-exported here so this package can be imported without verity-core installed.
"""

from verity_corpus.models.manifest import (
    DomainCategory,
    DomainTag,
    EntryStatus,
    ManifestEntry,
    SourceSpec,
    SourceType,
    compute_entry_id,
)
from verity_corpus.models.vrc import VRCEntry

__all__ = [
    "DomainCategory",
    "DomainTag",
    "EntryStatus",
    "ManifestEntry",
    "SourceSpec",
    "SourceType",
    "VRCEntry",
    "compute_entry_id",
]
