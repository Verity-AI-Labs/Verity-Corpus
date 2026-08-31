#!/usr/bin/env python3
"""Stub: expand a TRACE run into per-GameEnv manifest rows.

Upstream layout
---------------
https://github.com/ScalingIntelligence/TRACE synthesizes capability-targeted
training environments. It is not a static task dump. After a TRACE run the
inventory lives next to:

* generated ``capability_*_game.py`` files (each registers a ``GameSpec``
  via ``game_registry.register_game``)
* ``scenarios_v4_all.json`` (loaded by ``trace_v4_scenarios.py``; **not** in
  git — produced by phase 3 synth)

The one environment checked in today is
``swebench/qwen3.6_self_trace/capability_semantic_logic_precision_game.py``,
a single-turn SEARCH/REPLACE GameEnv. Core's ``verifiers`` adapter expects
``reward`` / ``rollout`` callables (``package.module:function``), not a
TRACE ``GameSpec.make_env``. Wrapping GameEnv as ``VerityEnv`` is Core work.

What this would emit
--------------------
One ``ManifestEntry`` per ``capability_*_game.py``, plus optional rows per
scenario in ``scenarios_v4_all.json`` if that file exists after a run.

This script enumerates those files so the expansion path is real. It stops
before writing YAML.

    python scripts/gen_trace.py --repo-root /path/to/TRACE
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TRACE_COMMIT = "d2db23085409555b3f13ea426f42d62cf0bbc43d"
TRACE_URL = "https://github.com/ScalingIntelligence/TRACE"


def iter_capability_games(repo_root: Path) -> list[Path]:
    """Return generated (and checked-in) ``capability_*_game.py`` files."""
    return sorted(repo_root.rglob("capability_*_game.py"))


def iter_scenarios(repo_root: Path) -> list[dict[str, Any]]:
    """Load ``scenarios_v4_all.json`` when a TRACE run has produced it."""
    matches = list(repo_root.rglob("scenarios_v4_all.json"))
    if not matches:
        return []
    payload = json.loads(matches[0].read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("scenarios", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [payload]
    return []


def entry_for_game(path: Path, *, repo_root: Path) -> dict[str, Any]:
    """Project a TRACE GameEnv module into a future manifest mapping."""
    rel = path.relative_to(repo_root).as_posix()
    stem = path.stem
    _payload = {
        "name": stem,
        "source": {
            "type": "git",
            "url": TRACE_URL,
            "path": rel,
            "commit": TRACE_COMMIT,
        },
        "domain": {"category": "code", "subcategory": "swe-bench"},
        "adapter": "verifiers",
        "adapter_config": {"timeout": 120},
        "metadata": {"game_module": rel},
    }
    del _payload
    raise NotImplementedError(
        f"no Core adapter can resolve TRACE GameEnv {rel!r} into a VerityEnv; "
        "verifiers adapter needs reward/rollout callables, not GameSpec.make_env"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="TRACE checkout, optionally after a synthesis run.",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve()
    if not repo_root.is_dir():
        print(f"repo root does not exist: {repo_root}", file=sys.stderr)
        return 1
    games = iter_capability_games(repo_root)
    scenarios = iter_scenarios(repo_root)
    print(
        f"found {len(games)} capability_*_game.py and {len(scenarios)} scenarios "
        f"under {repo_root}",
        file=sys.stderr,
    )
    if not games:
        print("no capability_*_game.py files; nothing to expand", file=sys.stderr)
        return 1
    entry_for_game(games[0], repo_root=repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
