# verity-corpus

The data layer for Verity Labs: a manifest-driven registry of RL training
environments, plus storage for audit scorecards and VRC (Verity Reward-hack
Corpus) exploit trajectories.

Verity-Corpus is **not** a clone of environments. Manifest entries point at
external git sources. Fetched checkouts live in `cache/` and are never committed.

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

## Pipeline

```
manifest YAML  →  fetch (cache/)  →  resolve (Core adapter)
                                         ↓
                              Core tools (RedTeam, Signal, Clean, Stable)
                                         ↓
                    results.py  →  scorecards/*.json  and  vrc/entries/{env_id}/
                                         ↓
                              verity-corpus sync-status
```

To feed Core's batch runner, export Corpus manifests into Core's flat YAML
shape (`id`, `format`, `domain`, `source`, `commit`, plus adapter fields):

```bash
verity-corpus export --output-dir /tmp/core-manifests
# Core:  load_corpus("/tmp/core-manifests")
```

## Registered benchmarks (samples)

These are representative slices, not full dumps. Comments in each YAML file
point at the upstream catalog to generate the rest later.

| Manifest | Upstream | Sample | Full catalog |
| --- | --- | ---: | --- |
| `manifests/terminal_wrench.yaml` | [few-sh/terminal-wrench](https://github.com/few-sh/terminal-wrench) @ `d8a2961` | 15 / 331 | `index/tasks.json`, `task_source_datasets.json` |
| `manifests/impossiblebench.yaml` | [safety-research/impossiblebench](https://github.com/safety-research/impossiblebench) + HF splits | 8 | `fjzzq2002/impossible_swebench` (349×3), `fjzzq2002/impossible_livecodebench` (103×3) |
| `manifests/trace.yaml` | [ScalingIntelligence/TRACE](https://github.com/ScalingIntelligence/TRACE) @ `d2db230` | 4 | generated `capability_*_game.py` + `scenarios_v4_all.json` after a TRACE run |
| `manifests/example.yaml` | smoke-test entry | 1 | — |

Terminal Wrench paths are `tasks/<id>/claude-opus-4.6/original_task` (the real
Terminal-Bench environment tree). ImpossibleBench task *data* is on HuggingFace
parquet splits, not per-task directories in the Inspect repo. TRACE synthesizes
GameEnvs; only one capability environment is checked in.

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

# Fetch environments into the local cache
verity-corpus fetch <env-id>
verity-corpus fetch --all
verity-corpus fetch --domain <category>

# Smoke-test: fetch, resolve through a Core adapter, print the VerityEnv
verity-corpus resolve <env-id>

# Summary counts by domain and status
verity-corpus status

# Write Core-flat YAML for load_corpus / the batch runner
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
manifests/     YAML registry entries (committed)
scorecards/    Core Scorecard JSON, named via scorecard_slug(env_id)
vrc/entries/   Exploit trajectories, nested by env id
cache/         Fetched environment sources (gitignored)
```
