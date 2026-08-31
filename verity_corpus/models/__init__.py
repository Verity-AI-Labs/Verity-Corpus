"""Pydantic data models for manifests and VRC entries.

:class:`~verity_core.scorecard.Scorecard` and :class:`~verity_core.scorecard.AxisValue`
are re-exported lazily so ``from verity_corpus.models import Scorecard`` works
without this module importing Core at load time. Store helpers from
:mod:`verity_corpus.scorecard_store` are re-exported the same way.
"""

from __future__ import annotations

from typing import Any

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
    "AxisValue",
    "DomainCategory",
    "DomainTag",
    "EntryStatus",
    "ManifestEntry",
    "Scorecard",
    "SourceSpec",
    "SourceType",
    "VRCEntry",
    "compute_entry_id",
    "exists",
    "list_scored",
    "load",
    "save",
]


def __getattr__(name: str) -> Any:
    if name in {"Scorecard", "AxisValue"}:
        from verity_core.scorecard import AxisValue, Scorecard

        return {"Scorecard": Scorecard, "AxisValue": AxisValue}[name]
    if name in {"exists", "list_scored", "load", "save"}:
        from verity_corpus import scorecard_store

        return getattr(scorecard_store, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
