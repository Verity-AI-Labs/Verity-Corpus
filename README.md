# verity-corpus

The manifest-driven environment registry for Verity Labs.

Verity-Corpus is **not** a clone of training environments. It is a registry of
manifest entries that point at external sources (git repos or local paths). The
environments themselves are fetched into a local cache and never committed.

The corpus stores three things:

1. **Manifests** — what environments exist and where they live
2. **Scorecards** — audit results per environment
3. **VRC entries** — exploit trajectories discovered by verity-redteam (the Verity Reward-hack Corpus)

`verity-corpus` depends on [`verity-core`](https://github.com/Verity-AI-Labs/Verity-Core)
for the `VerityEnv` protocol and adapter registry. Core does not depend on Corpus.
The resolver is the only coupling point between the two.

## Install

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
```

`verity-core` is pulled from GitHub (it is not on PyPI).

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
```

## Layout

```
manifests/     YAML registry entries (committed)
scorecards/    Per-environment audit JSON (committed as results land)
vrc/entries/   Exploit trajectories, nested by env id
cache/         Fetched environment sources (gitignored)
```
