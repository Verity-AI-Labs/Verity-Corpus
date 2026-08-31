"""VRC models: exploit trajectories discovered against a corpus environment."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

__all__ = ["VRCEntry"]


class VRCEntry(BaseModel):
    """One recorded exploit against a registered environment."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    env_id: str
    exploit_type: str
    trajectory: list[dict[str, Any]]
    hackability_curve: dict[int, float] | None = None
    model_id: str
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: str = ""

    def save(self, directory: Path) -> Path:
        """Write this entry to ``{directory}/{env_id}/{id}.json``, creating dirs as needed."""
        target = Path(directory) / self.env_id
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{self.id}.json"
        path.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, directory: Path, entry_id: str) -> VRCEntry:
        """Read ``{directory}/{env_id}/{entry_id}.json``.

        ``env_id`` is not part of the load signature, so this searches one
        level of subdirectories for ``{entry_id}.json``.
        """
        root = Path(directory)
        matches = list(root.glob(f"*/{entry_id}.json"))
        if not matches:
            raise FileNotFoundError(f"VRC entry {entry_id!r} not found under {root}")
        if len(matches) > 1:
            raise FileNotFoundError(
                f"VRC entry {entry_id!r} matched multiple paths under {root}: {matches}"
            )
        return cls.model_validate(json.loads(matches[0].read_text(encoding="utf-8")))
