# verity-corpus

The data layer for Verity Labs: a manifest-driven registry of RL training
environments, plus storage for audit scorecards and VRC (Verity Reward-hack
Corpus) exploit trajectories.

Verity-Corpus is **not** a clone of environments. Manifest entries point at
external git sources. Fetched checkouts live in `cache/` and are never committed.

Git clones are **shared per `(url, commit)`** under `cache/repos/{hash}/`.
An entry's environment root is `{shared_clone}/{entry.source.path}`, so 331
Terminal Wrench tasks reuse one checkout instead of cloning the repo 331 times.

## Relationship to Verity-Core

| | Corpus | Core |
| --- | --- | --- |
| Role | Data layer | Compute layer |
| Owns | Manifests, cached sources, scorecard files, VRC entries | `VerityEnv` protocol, adapters, sandbox, batch runner, scorecard *model* |
| Depends on | Core (resolver, scorecard type, `load_corpus` consumers) | Nothing in Corpus |

Core's [`Scorecard`](https://github.com/Verity-AI-Labs/Verity-Core/blob/main/src/verity_core/scorecard.py)
(13 axes: V1–V7, U1–U4, U6–U7) is canonical. Corpus persists those objects via
`verity_corpus.scorecard_store`; it does not define a second scorecard model.

The **resolver** is the only runtime coupling: `entry.adapter` → Core
`load_env` → `VerityEnv`. Registry, fetcher, and manifest models load without
Core installed; `resolve()` imports Core lazily.

Core's `ContainerEnv` cannot build from a Dockerfile (TODO in Core). Terminal
and `docker_test` entries must set `adapter_config.image` to a prebuilt tag.

## Pipeline

```
manifest YAML  →  fetch (cache/repos/{hash}/)  →  resolve (Core adapter)
                                                      ↓
                                           Core tools (RedTeam, Signal, Clean, Stable)
                                                      ↓
                             results.py  →  scorecards/*.json  and  vrc/entries/{env_id}/
                                                      ↓
                                           verity-corpus sync-status
```

To feed Core's batch runner, export Corpus manifests into Core's flat YAML
shape (`id`, `format`, `domain`, `source`, `commit`, plus adapter fields).
Catalog entries are omitted from the export.

```bash
verity-corpus export --output-dir /tmp/core-manifests
# Core:  load_corpus("/tmp/core-manifests")
```

## Registered benchmarks

| Manifest | Upstream | Entries | Notes |
| --- | --- | ---: | --- |
| `manifests/terminal_wrench.yaml` | [few-sh/terminal-wrench](https://github.com/few-sh/terminal-wrench) @ `d8a2961` | **331** (generated) | Auditable `terminal` tasks. Paths are `tasks/<id>/claude-opus-4.6/original_task`. Images: `verity-tw:<task_id>` after `scripts/build_images.py` (bakes `tests/` at `/tests`). |
| `manifests/impossiblebench.yaml` | [safety-research/impossiblebench](https://github.com/safety-research/impossiblebench) + HF splits | 8 **catalog** | Inspect factories + parquet splits (349 SWE × 3, 103 LCB × 3). Not `VerityEnv`s. |
| `manifests/trace.yaml` | [ScalingIntelligence/TRACE](https://github.com/ScalingIntelligence/TRACE) @ `d2db230` | 4 **catalog** | GameEnv / synth scripts. Not `VerityEnv`s. |
| `manifests/example.yaml` | smoke-test entry | 1 | Unpinned TW clone of `.` |

`status: catalog` means “benchmark-level pointer.” `verity-corpus fetch --all`
and `fetch --domain` skip those rows (`Skipping catalog entry {name}`). An
explicit env id still fetches. Catalog rows are excluded from `export`.

ImpossibleBench and TRACE need **Core adapter work** before per-instance
auditing is possible (SWE instance images + Inspect grading; TRACE `GameSpec`
wrapped as `VerityEnv`). The stub generators document that expansion; they
do not write fake env roots.

## Scripts

```bash
# After fetching Terminal Wrench, build the tags Core's TerminalAdapter needs.
# Does not run as part of the library. Requires Docker. Each image includes
# original_task/tests at /tests (Terminal-Bench mounts this at grade time).
python scripts/build_images.py --repo-root cache/repos/<hash>
python scripts/build_images.py --repo-root /path/to/terminal-wrench --task 5
python scripts/build_images.py --repo-root /path/to/terminal-wrench --dry-run

# Rebuild the 331-entry Terminal Wrench manifest from index/tasks.json
python scripts/gen_terminal_wrench.py --repo-root /path/to/terminal-wrench

# Stubs — parse upstream data, then NotImplementedError (no adapter yet)
python scripts/gen_impossiblebench.py --parquet data/conflicting-*.parquet --split conflicting
python scripts/gen_trace.py --repo-root /path/to/TRACE
```

## Install

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
```

`verity-core` is pulled from GitHub (it is not on PyPI). For container adapters
(`terminal`, `docker_test`), `adapter_config` must include a prebuilt Docker
`image` or Core's `ContainerEnv` will raise `ManifestError`.

## CLI

```bash
# Register an environment
verity-corpus add <source-url> <path> --domain <category> --adapter <adapter-name> \
    [--commit <hash>] [--name <label>] [--subcategory <sub>] \
    [--manifest-file <filename>] [--adapter-config <json-string>]

# List registered environments
verity-corpus list [--domain <category>] [--status <status>] [--adapter <adapter>]

# Fetch into cache/repos/{hash}/ (catalog rows skipped unless you pass an id)
verity-corpus fetch <env-id>
verity-corpus fetch --all
verity-corpus fetch --domain <category>

# Smoke-test: fetch, resolve through a Core adapter, print the VerityEnv
verity-corpus resolve <env-id>

# Summary counts by domain and status (includes a Catalog column)
verity-corpus status

# Write Core-flat YAML for load_corpus / the batch runner (skips catalog)
verity-corpus export --output-dir <path>

# Mark in-memory status audited when a scorecard exists on disk
verity-corpus sync-status
```

`--adapter-config` is a JSON object. For `terminal` and `docker_test` it must
include `"image"`.

## Tests

```bash
uv run pytest
uv run pytest -m integration   # Core bridge only
```

## Layout

```
manifests/              YAML registry entries (committed)
scripts/                Manual helpers (image build, manifest generators)
scorecards/             Core Scorecard JSON, named via scorecard_slug(env_id)
vrc/entries/            Exploit trajectories, nested by env id
cache/repos/{hash}/     Shared git clone per (url, commit) (gitignored)
```
