"""Command-line interface for the corpus registry, fetcher, and resolver."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Optional

import typer

from pydantic import ValidationError

from verity_corpus.fetcher import FetchError, fetch as fetch_entry
from verity_corpus.models.manifest import DomainTag, ManifestEntry, SourceSpec
from verity_corpus.registry import CorpusRegistry, RegistryError
from verity_corpus.resolver import ResolveError, resolve as resolve_entry

app = typer.Typer(help="Verity-Corpus: manifest-driven environment registry.")

_STATUS_COLUMNS = ("registered", "fetched", "auditing", "audited", "broken")
_STATUS_HEADERS = ("Registered", "Fetched", "Auditing", "Audited", "Broken")


def _registry() -> CorpusRegistry:
    return CorpusRegistry()


def _select(
    registry: CorpusRegistry,
    *,
    env_id: str | None = None,
    domain: str | None = None,
    status: str | None = None,
    adapter: str | None = None,
) -> list[ManifestEntry]:
    if env_id is not None:
        entry = registry.by_id(env_id)
        if entry is None:
            raise typer.BadParameter(f"unknown environment id {env_id!r}")
        return [entry]
    entries = registry.all()
    if domain is not None:
        entries = [e for e in entries if e.domain.category == domain]
    if status is not None:
        entries = [e for e in entries if e.status == status]
    if adapter is not None:
        entries = [e for e in entries if e.adapter == adapter]
    return entries


@app.command()
def add(
    source_url: str = typer.Argument(..., help="Git URL of the environment source."),
    path: str = typer.Argument(..., help="Path within the repo to the environment root."),
    domain: str = typer.Option(..., "--domain", help="Domain category (terminal, browser, ...)."),
    adapter: str = typer.Option(..., "--adapter", help="verity-core adapter name."),
    commit: Optional[str] = typer.Option(None, "--commit", help="Pinned commit hash."),
    name: Optional[str] = typer.Option(None, "--name", help="Human-readable label."),
    subcategory: Optional[str] = typer.Option(None, "--subcategory"),
    manifest_file: str = typer.Option("manual.yaml", "--manifest-file"),
    adapter_config: Optional[str] = typer.Option(
        None,
        "--adapter-config",
        help=(
            "JSON object of adapter-specific parameters. For terminal and "
            "docker_test adapters this must include an 'image' key (the prebuilt "
            "Docker image Core's ContainerEnv requires)."
        ),
    ),
) -> None:
    """Register an environment and write it to a manifest YAML file."""
    config_payload: dict = {}
    if adapter_config:
        try:
            parsed = json.loads(adapter_config)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"adapter-config is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise typer.BadParameter("adapter-config must be a JSON object")
        config_payload = parsed

    try:
        entry = ManifestEntry.create(
            name=name or path,
            source=SourceSpec(type="git", url=source_url, commit=commit, path=path),
            domain=DomainTag(category=domain, subcategory=subcategory),  # type: ignore[arg-type]
            adapter=adapter,
            adapter_config=config_payload,
        )
    except ValidationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        _registry().add_entry(entry, manifest_file)
    except RegistryError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(entry.id)


@app.command("list")
def list_entries(
    domain: Optional[str] = typer.Option(None, "--domain"),
    status: Optional[str] = typer.Option(None, "--status"),
    adapter: Optional[str] = typer.Option(None, "--adapter"),
) -> None:
    """Print matching environments: id, name, domain, adapter, status."""
    entries = _select(_registry(), domain=domain, status=status, adapter=adapter)
    if not entries:
        typer.echo("No matching environments.")
        return
    header = f"{'ID':<12}  {'Name':<40}  {'Domain':<20}  {'Adapter':<16}  Status"
    typer.echo(header)
    for entry in entries:
        domain_label = entry.domain.category
        if entry.domain.subcategory:
            domain_label = f"{entry.domain.category}/{entry.domain.subcategory}"
        typer.echo(
            f"{entry.id:<12}  {entry.name:<40.40}  {domain_label:<20.20}  "
            f"{entry.adapter:<16.16}  {entry.status}"
        )


@app.command()
def fetch(
    env_id: Optional[str] = typer.Argument(None, help="Environment id to fetch."),
    all_entries: bool = typer.Option(False, "--all", help="Fetch every registered environment."),
    domain: Optional[str] = typer.Option(None, "--domain", help="Fetch all entries in this domain."),
) -> None:
    """Fetch matching environments into the local cache."""
    if sum(bool(x) for x in (env_id, all_entries, domain)) != 1:
        typer.echo("Specify an env id, --all, or --domain.", err=True)
        raise typer.Exit(code=1)

    registry = _registry()
    if all_entries:
        entries = registry.all()
    elif domain is not None:
        entries = registry.by_domain(domain)
    else:
        found = registry.by_id(env_id)  # type: ignore[arg-type]
        if found is None:
            typer.echo(f"error: unknown environment id {env_id!r}", err=True)
            raise typer.Exit(code=1)
        entries = [found]

    failures = 0
    for entry in entries:
        typer.echo(f"Fetching {entry.name} ({entry.id})...", nl=False)
        try:
            fetch_entry(entry)
        except FetchError as exc:
            typer.echo(f" FAILED: {exc}")
            registry.update_status(entry.id, "broken")
            failures += 1
        else:
            typer.echo(" done")
            registry.update_status(entry.id, "fetched")
    if failures:
        raise typer.Exit(code=1)


@app.command("resolve")
def resolve_cmd(env_id: str = typer.Argument(..., help="Environment id to resolve.")) -> None:
    """Fetch if needed, resolve to a VerityEnv, and print its type (smoke test)."""
    registry = _registry()
    entry = registry.by_id(env_id)
    if entry is None:
        typer.echo(f"error: unknown environment id {env_id!r}", err=True)
        raise typer.Exit(code=1)

    try:
        fetch_entry(entry)
        registry.update_status(entry.id, "fetched")
        env = resolve_entry(entry)
    except (FetchError, ResolveError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"{type(env).__module__}.{type(env).__name__}")
    typer.echo(repr(env))
    closer = getattr(env, "close", None)
    if callable(closer):
        closer()


@app.command()
def status() -> None:
    """Print counts by domain category and by status."""
    entries = _registry().all()
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {s: 0 for s in _STATUS_COLUMNS})
    totals = {s: 0 for s in _STATUS_COLUMNS}
    for entry in entries:
        counts[entry.domain.category][entry.status] += 1
        totals[entry.status] += 1

    name_width = max([6, *(len(name) for name in counts)], default=6)
    col_width = 10

    def fmt_row(label: str, values: dict[str, int]) -> str:
        cells = "".join(f"{values[s]:>{col_width}}" for s in _STATUS_COLUMNS)
        return f"{label:<{name_width}}{cells}{sum(values.values()):>{col_width}}"

    header = f"{'Domain':<{name_width}}" + "".join(
        f"{h:>{col_width}}" for h in _STATUS_HEADERS
    ) + f"{'Total':>{col_width}}"
    typer.echo(header)
    for category in sorted(counts):
        typer.echo(fmt_row(category, counts[category]))
    typer.echo(fmt_row("Total", totals))


if __name__ == "__main__":
    app()
