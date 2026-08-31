"""Tests for CorpusRegistry YAML loading, queries, and mutation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from verity_corpus.models.manifest import DomainTag, ManifestEntry, SourceSpec
from verity_corpus.registry import CorpusRegistry, DuplicateEntryError

SHARED_YAML = """\
source_defaults:
  type: git
  url: https://github.com/example/bench
  commit: deadbeef
entries:
  - name: inherited
    path: tasks/a
    domain:
      category: terminal
      subcategory: bash
    adapter: terminal
  - name: override-commit
    path: tasks/b
    commit: cafebabe
    domain:
      category: browser
    adapter: docker_test
    status: audited
"""

SECOND_YAML = """\
entries:
  - name: local-env
    source:
      type: local
      path: /tmp/somewhere
    domain:
      category: code
    adapter: docker_test
    status: fetched
"""


def _write(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


class TestCorpusRegistryLoad:
    def test_loads_entries_from_yaml(self, tmp_path: Path) -> None:
        _write(tmp_path, "bench.yaml", SHARED_YAML)
        registry = CorpusRegistry(tmp_path)
        assert len(registry.all()) == 2
        names = {e.name for e in registry.all()}
        assert names == {"inherited", "override-commit"}

    def test_skips_schema_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "_schema.yaml", "entries: []\n")
        _write(tmp_path, "bench.yaml", SHARED_YAML)
        registry = CorpusRegistry(tmp_path)
        assert len(registry.all()) == 2

    def test_source_defaults_inherited_and_overridden(self, tmp_path: Path) -> None:
        _write(tmp_path, "bench.yaml", SHARED_YAML)
        registry = CorpusRegistry(tmp_path)
        inherited = next(e for e in registry.all() if e.name == "inherited")
        overridden = next(e for e in registry.all() if e.name == "override-commit")
        assert inherited.source.url == "https://github.com/example/bench"
        assert inherited.source.commit == "deadbeef"
        assert inherited.source.path == "tasks/a"
        assert inherited.source.type == "git"
        assert overridden.source.url == "https://github.com/example/bench"
        assert overridden.source.commit == "cafebabe"
        assert overridden.source.path == "tasks/b"


class TestCorpusRegistryQuery:
    @pytest.fixture()
    def registry(self, tmp_path: Path) -> CorpusRegistry:
        _write(tmp_path, "a.yaml", SHARED_YAML)
        _write(tmp_path, "b.yaml", SECOND_YAML)
        return CorpusRegistry(tmp_path)

    def test_by_id(self, registry: CorpusRegistry) -> None:
        entry = next(e for e in registry.all() if e.name == "inherited")
        assert registry.by_id(entry.id) is entry
        assert registry.by_id("missing") is None

    def test_by_domain(self, registry: CorpusRegistry) -> None:
        terminal = registry.by_domain("terminal")
        assert [e.name for e in terminal] == ["inherited"]
        assert registry.by_domain("terminal", "bash") == terminal
        assert registry.by_domain("terminal", "zsh") == []
        assert [e.name for e in registry.by_domain("browser")] == ["override-commit"]

    def test_by_status(self, registry: CorpusRegistry) -> None:
        assert [e.name for e in registry.by_status("audited")] == ["override-commit"]
        assert [e.name for e in registry.by_status("fetched")] == ["local-env"]
        registered = registry.by_status("registered")
        assert [e.name for e in registered] == ["inherited"]

    def test_by_adapter(self, registry: CorpusRegistry) -> None:
        assert [e.name for e in registry.by_adapter("terminal")] == ["inherited"]
        docker = {e.name for e in registry.by_adapter("docker_test")}
        assert docker == {"override-commit", "local-env"}


class TestCorpusRegistryDuplicatesAndAdd:
    def test_duplicate_ids_across_files_raise(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.yaml", SHARED_YAML)
        _write(tmp_path, "b.yaml", SHARED_YAML)
        with pytest.raises(DuplicateEntryError, match="duplicate environment id"):
            CorpusRegistry(tmp_path)

    def test_add_entry_writes_yaml_and_index(self, tmp_path: Path) -> None:
        registry = CorpusRegistry(tmp_path)
        entry = ManifestEntry.create(
            name="new-env",
            source=SourceSpec(
                type="git",
                url="https://github.com/example/env",
                commit="abc",
                path=".",
            ),
            domain=DomainTag(category="api"),
            adapter="verifiers",
        )
        registry.add_entry(entry, "manual.yaml")
        assert registry.by_id(entry.id) is entry
        written = yaml.safe_load((tmp_path / "manual.yaml").read_text(encoding="utf-8"))
        assert written["entries"][0]["id"] == entry.id
        assert written["entries"][0]["name"] == "new-env"
        reloaded = CorpusRegistry(tmp_path)
        assert reloaded.by_id(entry.id) is not None
        assert reloaded.by_id(entry.id).name == "new-env"

    def test_update_status_is_in_memory_only(self, tmp_path: Path) -> None:
        _write(tmp_path, "bench.yaml", SHARED_YAML)
        registry = CorpusRegistry(tmp_path)
        entry = next(e for e in registry.all() if e.name == "inherited")
        updated = registry.update_status(entry.id, "fetched")
        assert updated.status == "fetched"
        assert registry.by_id(entry.id).status == "fetched"
        on_disk = yaml.safe_load((tmp_path / "bench.yaml").read_text(encoding="utf-8"))
        statuses = [e.get("status") for e in on_disk["entries"]]
        assert "fetched" not in statuses
