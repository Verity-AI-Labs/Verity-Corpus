"""Manifest models: where an environment lives and how to load it.

``ManifestEntry.id`` is derived from the source pin (url, path, commit) so the
same environment at the same revision always receives the same identifier.
Callers must not set it by hand; use :meth:`ManifestEntry.create` or let the
validator compute it.

``status="catalog"`` marks a benchmark-level pointer (a parquet split, a task
factory, a generator script). Catalog entries are not individually auditable
environments; ``fetch --all`` skips them.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

SourceType = Literal["git", "local"]
DomainCategory = Literal["terminal", "browser", "gui", "code", "api", "math", "other"]
EntryStatus = Literal[
    "registered", "fetched", "auditing", "audited", "broken", "catalog"
]

__all__ = [
    "DomainCategory",
    "DomainTag",
    "EntryStatus",
    "ManifestEntry",
    "SourceSpec",
    "SourceType",
    "compute_entry_id",
]


def compute_entry_id(source: SourceSpec) -> str:
    """Return the first 12 hex chars of sha256(url, path, commit-or-HEAD)."""
    payload = "\0".join((source.url or "", source.path, source.commit or "HEAD"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class SourceSpec(BaseModel):
    """Where an environment lives externally."""

    type: SourceType
    url: str | None = None
    commit: str | None = None
    path: str

    @model_validator(mode="after")
    def _require_url_for_git(self) -> Self:
        if self.type == "git" and not self.url:
            raise ValueError("git sources require a url")
        return self


class DomainTag(BaseModel):
    """Categorizes the environment for filtering and reporting."""

    category: DomainCategory
    subcategory: str | None = None


class ManifestEntry(BaseModel):
    """One environment at one pinned revision, or a catalog pointer.

    Catalog entries (``status="catalog"``) record where a benchmark's data lives
    without claiming that ``path`` is a resolvable ``VerityEnv``.
    """

    id: str = ""
    name: str
    source: SourceSpec
    domain: DomainTag
    adapter: str
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: EntryStatus = "registered"

    @model_validator(mode="after")
    def _assign_deterministic_id(self) -> Self:
        self.id = compute_entry_id(self.source)
        return self

    @classmethod
    def create(
        cls,
        *,
        name: str,
        source: SourceSpec,
        domain: DomainTag,
        adapter: str,
        adapter_config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        status: EntryStatus = "registered",
    ) -> ManifestEntry:
        """Build an entry, computing ``id`` from the source pin and stamping ``added_at``."""
        return cls(
            name=name,
            source=source,
            domain=domain,
            adapter=adapter,
            adapter_config=adapter_config or {},
            metadata=metadata or {},
            added_at=datetime.now(UTC),
            status=status,
        )
