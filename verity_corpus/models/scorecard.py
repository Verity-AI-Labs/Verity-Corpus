"""Scorecard models: per-environment audit measurements.

None on an axis means unscored. 0.0 means measured zero. Those two states must
remain distinguishable through JSON round-trips — ``model_dump(mode="json")``
serializes None as JSON ``null``, never as 0 or an omitted key.

The scorecard stores all 14 axis slots (V1–V7 and U1–U7). U5 is the downstream
outcome predicted by later regression work and is excluded from feature vectors
in Phase 2; it still belongs on the scorecard so the record is complete.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

VALIDITY_AXES: tuple[str, ...] = ("V1", "V2", "V3", "V4", "V5", "V6", "V7")
UTILITY_AXES: tuple[str, ...] = ("U1", "U2", "U3", "U4", "U5", "U6", "U7")
ALL_AXES: tuple[str, ...] = VALIDITY_AXES + UTILITY_AXES
"""All 14 scorecard slots. U5 is stored here but excluded from later feature vectors."""

__all__ = [
    "ALL_AXES",
    "UTILITY_AXES",
    "VALIDITY_AXES",
    "AxisScore",
    "ScoreCard",
]


class AxisScore(BaseModel):
    """One axis measurement. ``value=None`` is unscored; ``value=0.0`` is a real zero."""

    value: float | None = None
    confidence: float | None = None
    evidence: list[str] = Field(default_factory=list)
    measured_at: datetime | None = None


class ScoreCard(BaseModel):
    """Audit results for a single environment, keyed by axis id."""

    env_id: str
    axes: dict[str, AxisScore]

    @classmethod
    def empty(cls, env_id: str) -> ScoreCard:
        """Construct a scorecard with every axis present and unscored."""
        return cls(env_id=env_id, axes={axis: AxisScore() for axis in ALL_AXES})

    def save(self, directory: Path) -> Path:
        """Write this scorecard to ``{directory}/{env_id}.json``."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.env_id}.json"
        path.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, directory: Path, env_id: str) -> ScoreCard:
        """Read ``{directory}/{env_id}.json``."""
        path = Path(directory) / f"{env_id}.json"
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
