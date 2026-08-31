"""In-memory index of corpus manifest entries loaded from YAML files.

Each file may declare ``source_defaults`` so a benchmark of hundreds of
environments does not repeat the same git URL and commit on every entry.
Entry-level source fields override those defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from verity_corpus import config
from verity_corpus.models.manifest import ManifestEntry

SOURCE_FIELDS = ("type", "url", "commit", "path")
SCHEMA_FILENAME = "_schema.yaml"

__all__ = ["CorpusRegistry", "DuplicateEntryError", "RegistryError"]


class RegistryError(ValueError):
    """Raised when a manifest file cannot be loaded into the registry."""


class DuplicateEntryError(RegistryError):
    """Raised when two manifest files (or entries) share the same environment id."""


def _as_mapping(value: object, *, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RegistryError(f"{context}: expected a mapping, got {type(value).__name__}")
    return value


def _merge_source(defaults: dict[str, Any], raw_entry: dict[str, Any]) -> dict[str, Any]:
    """Build a SourceSpec dict: defaults, then nested ``source``, then top-level fields."""
    merged = dict(defaults)
    nested = raw_entry.get("source")
    if nested is not None:
        merged.update(_as_mapping(nested, context="entry.source"))
    for field in SOURCE_FIELDS:
        if field in raw_entry:
            merged[field] = raw_entry[field]
    return merged


def _entry_from_raw(raw_entry: dict[str, Any], defaults: dict[str, Any]) -> ManifestEntry:
    data = dict(raw_entry)
    source = _merge_source(defaults, data)
    for field in SOURCE_FIELDS:
        data.pop(field, None)
    data["source"] = source
    domain = data.get("domain")
    if isinstance(domain, str):
        data["domain"] = {"category": domain}
    return ManifestEntry.model_validate(data)


class CorpusRegistry:
    """Central index of :class:`ManifestEntry` objects, keyed by environment id."""

    def __init__(self, manifests_dir: Path | None = None) -> None:
        self.manifests_dir = Path(manifests_dir) if manifests_dir is not None else config.MANIFESTS_DIR
        self._entries: dict[str, ManifestEntry] = {}
        self._files: dict[str, str] = {}
        self._load()

    def _manifest_files(self) -> list[Path]:
        if not self.manifests_dir.is_dir():
            return []
        return sorted(
            path
            for path in self.manifests_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".yaml", ".yml"}
            and path.name != SCHEMA_FILENAME
            and not path.name.startswith(".")
        )

    def _load(self) -> None:
        for path in self._manifest_files():
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise RegistryError(f"{path}: not valid YAML: {exc}") from exc
            if loaded is None:
                continue
            document = _as_mapping(loaded, context=str(path))
            defaults = _as_mapping(
                document.get("source_defaults"), context=f"{path} source_defaults"
            )
            raw_entries = document.get("entries")
            if raw_entries is None:
                # A file that is itself a single flattened entry (no `entries` list).
                if "name" in document or "source" in document or "path" in document:
                    raw_entries = [document]
                else:
                    raw_entries = []
            if not isinstance(raw_entries, list):
                raise RegistryError(f"{path}: 'entries' must be a list")
            for index, raw in enumerate(raw_entries):
                raw_map = _as_mapping(raw, context=f"{path} entries[{index}]")
                entry = _entry_from_raw(raw_map, defaults)
                if entry.id in self._entries:
                    raise DuplicateEntryError(
                        f"duplicate environment id {entry.id!r} in {path}; "
                        f"already defined in {self._files[entry.id]}"
                    )
                self._entries[entry.id] = entry
                self._files[entry.id] = str(path)

    def all(self) -> list[ManifestEntry]:
        return list(self._entries.values())

    def by_id(self, env_id: str) -> ManifestEntry | None:
        return self._entries.get(env_id)

    def by_domain(
        self, category: str, subcategory: str | None = None
    ) -> list[ManifestEntry]:
        matches = [e for e in self._entries.values() if e.domain.category == category]
        if subcategory is not None:
            matches = [e for e in matches if e.domain.subcategory == subcategory]
        return matches

    def by_status(self, status: str) -> list[ManifestEntry]:
        return [e for e in self._entries.values() if e.status == status]

    def by_adapter(self, adapter: str) -> list[ManifestEntry]:
        return [e for e in self._entries.values() if e.adapter == adapter]

    def add_entry(self, entry: ManifestEntry, manifest_file: str) -> None:
        """Append ``entry`` to ``manifest_file`` (created if missing) and to the index."""
        if entry.id in self._entries:
            raise DuplicateEntryError(
                f"duplicate environment id {entry.id!r}; "
                f"already defined in {self._files[entry.id]}"
            )
        path = Path(manifest_file)
        if not path.is_absolute():
            path = self.manifests_dir / path
        if path.suffix.lower() not in {".yaml", ".yml"}:
            path = path.with_suffix(".yaml")

        document: dict[str, Any]
        if path.is_file():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            document = _as_mapping(loaded, context=str(path))
            entries = document.setdefault("entries", [])
            if not isinstance(entries, list):
                raise RegistryError(f"{path}: 'entries' must be a list")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            document = {"entries": []}
            entries = document["entries"]

        dumped = entry.model_dump(mode="json", exclude={"id"})
        entries.append(dumped)
        path.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        self._entries[entry.id] = entry
        self._files[entry.id] = str(path)

    def update_status(self, env_id: str, status: str) -> ManifestEntry:
        """Update in-memory status only. Manifest YAML is not rewritten."""
        entry = self._entries.get(env_id)
        if entry is None:
            raise RegistryError(f"unknown environment id {env_id!r}")
        updated = entry.model_copy(update={"status": status})
        self._entries[env_id] = updated
        return updated

    def export_for_core(self, output_dir: Path) -> list[Path]:
        """Write Core-compatible flat YAML manifests into ``output_dir``.

        Each source manifest becomes one YAML file containing a list of mappings
        with the fields :func:`verity_core.corpus.load_corpus` requires: ``id``,
        ``format``, ``domain`` (mapped onto Core's Domain), ``source``, ``commit``,
        ``instructions``, plus flattened ``adapter_config``. Catalog entries are
        omitted — they are not loadable as ``VerityEnv``s.
        """
        from verity_corpus.resolver import core_manifest

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        grouped: dict[str, list[ManifestEntry]] = {}
        for entry in self.all():
            if entry.status == "catalog":
                continue
            stem = Path(self._files.get(entry.id, "manual.yaml")).stem
            grouped.setdefault(stem, []).append(entry)

        written: list[Path] = []
        for stem, entries in grouped.items():
            payload = [core_manifest(entry) for entry in entries]
            path = output_dir / f"{stem}.yaml"
            path.write_text(
                yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            written.append(path)
        return written
