"""Default paths for the corpus tree.

``CORPUS_ROOT`` is the repository root: either ``VERITY_CORPUS_ROOT`` or the
nearest ancestor of this file that contains a ``pyproject.toml``. All other
paths are derived from it so a checkout, an editable install, and a test
tmpdir that points the env var elsewhere all resolve consistently.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "CACHE_DIR",
    "CORPUS_ROOT",
    "MANIFESTS_DIR",
    "SCORECARDS_DIR",
    "VRC_DIR",
]


def _find_corpus_root() -> Path:
    override = os.environ.get("VERITY_CORPUS_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(
        "could not locate corpus root: no pyproject.toml above "
        f"{here} and VERITY_CORPUS_ROOT is unset"
    )


CORPUS_ROOT: Path = _find_corpus_root()
MANIFESTS_DIR: Path = CORPUS_ROOT / "manifests"
SCORECARDS_DIR: Path = CORPUS_ROOT / "scorecards"
VRC_DIR: Path = CORPUS_ROOT / "vrc" / "entries"
CACHE_DIR: Path = CORPUS_ROOT / "cache"
