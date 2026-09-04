#!/usr/bin/env python3
"""Stub: expand ImpossibleBench parquet splits into per-instance manifest rows.

Upstream data
-------------
HuggingFace datasets (git LFS / parquet), not per-task directories:

* ``fjzzq2002/impossible_swebench`` — splits ``original``, ``oneoff``,
  ``conflicting`` (349 rows each). Columns include ``instance_id``, ``repo``,
  ``base_commit``, ``problem_statement``, ``patch``, ``test_patch``,
  ``impossible_type``, ``difficulty``.
* ``fjzzq2002/impossible_livecodebench`` — same three splits (103 rows each).
  Columns include ``task_id``, ``prompt``, ``test``, ``original_test``,
  ``impossible_type``, ``entry_point``.

The Inspect harness at https://github.com/safety-research/impossiblebench
loads those HF splits (see ``src/impossiblebench/swebench_tasks.py`` and
``livecodebench_tasks.py``). SWE instance images are
``swebench/sweb.eval.{arch}.{instance_id with __ → _}:latest``. LiveCodeBench
uses ``aisiuk/inspect-tool-support`` plus ``compose.yaml``.

What this would emit
--------------------
One ``ManifestEntry`` per ``(split, instance_id)`` with a unique ``path``
(instance id must be in the source pin, otherwise Corpus ids collide because
they hash ``url + path + commit``). Adapter would be ``docker_test``.

This script parses the parquet so the expansion path is real. It stops before
writing YAML: there is no Core adapter that turns a SWE-bench instance image
plus a hidden-test grading protocol into a ``VerityEnv``. Do not add that
adapter here.

    python scripts/gen_impossiblebench.py --parquet path/to/conflicting.parquet --split conflicting
"""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path
from typing import Any

SPLITS = ("original", "oneoff", "conflicting")
SWE_HF = "https://huggingface.co/datasets/fjzzq2002/impossible_swebench"
LCB_HF = "https://huggingface.co/datasets/fjzzq2002/impossible_livecodebench"


def swe_image_for(instance_id: str) -> str:
    """Mirror ``get_remote_docker_image_from_id`` in the Inspect harness."""
    arch = platform.machine()
    if arch == "x86_64":
        arch = "amd64"
    slug = instance_id.replace("__", "_")
    return f"swebench/sweb.eval.{arch}.{slug}:latest"


def iter_parquet_rows(path: Path) -> list[dict[str, Any]]:
    """Read an ImpossibleBench split parquet into row dicts.

    Requires ``pyarrow`` (not a Corpus runtime dependency).
    """
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise SystemExit(
            "pyarrow is required to read ImpossibleBench parquet files. "
            "Install it in the environment that runs this generator."
        ) from exc
    table = pq.read_table(path)
    return table.to_pylist()


def entry_for_swe_instance(row: dict[str, Any], *, split: str) -> dict[str, Any]:
    """Project one SWE-bench parquet row into a future manifest mapping.

    Raises ``NotImplementedError`` because Core has no adapter for this shape.
    """
    instance_id = str(row["instance_id"])
    image = swe_image_for(instance_id)
    _payload = {
        "name": f"impossible-swebench-{split}-{instance_id}",
        "source": {
            "type": "git",
            "url": SWE_HF,
            "path": f"instances/{split}/{instance_id}",
            "commit": None,
        },
        "domain": {"category": "code", "subcategory": "swe-bench"},
        "adapter": "docker_test",
        "adapter_config": {"image": image, "timeout": 600},
        "metadata": {
            "split": split,
            "instance_id": instance_id,
            "repo": row.get("repo"),
            "impossible_type": row.get("impossible_type"),
            "difficulty": row.get("difficulty"),
        },
    }
    del _payload
    raise NotImplementedError(
        f"no Core adapter can resolve SWE-bench instance {instance_id!r} "
        f"(image {image}) into a VerityEnv; docker_test would need the "
        "Inspect hidden-test grading protocol, which does not belong in Corpus"
    )


def entry_for_lcb_instance(row: dict[str, Any], *, split: str) -> dict[str, Any]:
    """Project one LiveCodeBench parquet row into a future manifest mapping."""
    task_id = str(row["task_id"])
    _payload = {
        "name": f"impossible-livecodebench-{split}-{task_id}",
        "source": {
            "type": "git",
            "url": LCB_HF,
            "path": f"instances/{split}/{task_id}",
            "commit": None,
        },
        "domain": {"category": "code", "subcategory": "livecodebench"},
        "adapter": "docker_test",
        "adapter_config": {
            "image": "aisiuk/inspect-tool-support",
            "timeout": 120,
        },
        "metadata": {
            "split": split,
            "task_id": task_id,
            "impossible_type": row.get("impossible_type"),
            "entry_point": row.get("entry_point"),
        },
    }
    del _payload
    raise NotImplementedError(
        f"no Core adapter can resolve LiveCodeBench task {task_id!r} into a "
        "VerityEnv; the Inspect compose + hidden tests are not a docker_test "
        "environment Corpus can emit"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--parquet", type=Path, required=True, help="Split parquet file."
    )
    parser.add_argument("--split", required=True, choices=SPLITS)
    parser.add_argument(
        "--kind",
        choices=("swe", "lcb"),
        default="swe",
        help="SWE-bench instance_id rows vs LiveCodeBench task_id rows.",
    )
    args = parser.parse_args(argv)
    rows = iter_parquet_rows(args.parquet.expanduser())
    print(
        f"read {len(rows)} rows from {args.parquet} (split={args.split})",
        file=sys.stderr,
    )
    builder = entry_for_swe_instance if args.kind == "swe" else entry_for_lcb_instance
    builder(rows[0], split=args.split)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
