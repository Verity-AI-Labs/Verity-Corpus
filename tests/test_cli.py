"""CLI tests for fetch skipping catalog entries."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from verity_corpus.cli import app
from verity_corpus.registry import CorpusRegistry

runner = CliRunner()


def _write_mixed(manifests: Path) -> None:
    manifests.mkdir(parents=True)
    (manifests / "mixed.yaml").write_text(
        """\
entries:
  - name: real-env
    source:
      type: local
      path: /tmp/real-env
    domain:
      category: terminal
    adapter: terminal
    status: registered
  - name: pointer
    source:
      type: local
      path: /tmp/pointer.parquet
    domain:
      category: code
    adapter: docker_test
    status: catalog
""",
        encoding="utf-8",
    )


class TestFetchSkipsCatalog:
    def test_fetch_all_skips_catalog_and_fetches_the_rest(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        manifests = tmp_path / "manifests"
        _write_mixed(manifests)
        monkeypatch.setattr("verity_corpus.cli._registry", lambda: CorpusRegistry(manifests))
        fetched: list[str] = []

        def fake_fetch(entry, cache_dir=None):
            fetched.append(entry.name)
            return Path(entry.source.path)

        with patch("verity_corpus.cli.fetch_entry", side_effect=fake_fetch):
            result = runner.invoke(app, ["fetch", "--all"])

        assert result.exit_code == 0, result.output
        assert fetched == ["real-env"]
        assert "Skipping catalog entry pointer" in result.output
        assert "Fetching real-env" in result.output

    def test_fetch_domain_skips_catalog(self, tmp_path: Path, monkeypatch) -> None:
        manifests = tmp_path / "manifests"
        _write_mixed(manifests)
        monkeypatch.setattr("verity_corpus.cli._registry", lambda: CorpusRegistry(manifests))
        fetched: list[str] = []

        def fake_fetch(entry, cache_dir=None):
            fetched.append(entry.name)
            return Path(entry.source.path)

        with patch("verity_corpus.cli.fetch_entry", side_effect=fake_fetch):
            result = runner.invoke(app, ["fetch", "--domain", "code"])

        assert result.exit_code == 0, result.output
        assert fetched == []
        assert "Skipping catalog entry pointer" in result.output

    def test_explicit_env_id_fetches_catalog(self, tmp_path: Path, monkeypatch) -> None:
        manifests = tmp_path / "manifests"
        _write_mixed(manifests)
        registry = CorpusRegistry(manifests)
        catalog = next(e for e in registry.all() if e.status == "catalog")
        monkeypatch.setattr("verity_corpus.cli._registry", lambda: CorpusRegistry(manifests))
        fetched: list[str] = []

        def fake_fetch(entry, cache_dir=None):
            fetched.append(entry.name)
            return Path(entry.source.path)

        with patch("verity_corpus.cli.fetch_entry", side_effect=fake_fetch):
            result = runner.invoke(app, ["fetch", catalog.id])

        assert result.exit_code == 0, result.output
        assert fetched == ["pointer"]
        assert "Skipping catalog entry" not in result.output


class TestStatusCatalogColumn:
    def test_status_table_includes_catalog_column(self, tmp_path: Path, monkeypatch) -> None:
        manifests = tmp_path / "manifests"
        _write_mixed(manifests)
        monkeypatch.setattr("verity_corpus.cli._registry", lambda: CorpusRegistry(manifests))
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0, result.output
        assert "Catalog" in result.output

