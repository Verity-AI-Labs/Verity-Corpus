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
        monkeypatch.setattr(
            "verity_corpus.cli._registry", lambda: CorpusRegistry(manifests)
        )
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
        monkeypatch.setattr(
            "verity_corpus.cli._registry", lambda: CorpusRegistry(manifests)
        )
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
        monkeypatch.setattr(
            "verity_corpus.cli._registry", lambda: CorpusRegistry(manifests)
        )
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
    def test_status_table_includes_catalog_column(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        manifests = tmp_path / "manifests"
        _write_mixed(manifests)
        monkeypatch.setattr(
            "verity_corpus.cli._registry", lambda: CorpusRegistry(manifests)
        )
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0, result.output
        assert "Catalog" in result.output


def _write_hack_fixture(tmp_path: Path) -> tuple[Path, str, str]:
    """Return (manifests_dir, present_task_name, absent_task_name)."""
    import json

    manifests = tmp_path / "manifests"
    present_root = tmp_path / "present"
    trajectory = {
        "steps": [
            {
                "tool_calls": [
                    {
                        "function_name": "bash",
                        "arguments": {"command": "cat tests/test.sh"},
                    }
                ]
            }
        ]
    }
    traj_path = (
        present_root
        / "hack_trajectories"
        / "v5"
        / "trial"
        / "agent"
        / "trajectory.json"
    )
    traj_path.parent.mkdir(parents=True)
    traj_path.write_text(json.dumps(trajectory) + "\n", encoding="utf-8")
    reward = (
        present_root / "hack_trajectories" / "v5" / "trial" / "verifier" / "reward.txt"
    )
    reward.parent.mkdir(parents=True)
    reward.write_text("1\n", encoding="utf-8")
    (tmp_path / "absent").mkdir()
    manifests.mkdir()
    (manifests / "tw.yaml").write_text(
        f"""\
entries:
  - name: present-task
    source:
      type: local
      path: {present_root}
    domain:
      category: terminal
    adapter: terminal
  - name: absent-task
    source:
      type: local
      path: {tmp_path / "absent"}
    domain:
      category: terminal
    adapter: terminal
  - name: catalog-pointer
    source:
      type: local
      path: {tmp_path / "catalog.parquet"}
    domain:
      category: code
    adapter: docker_test
    status: catalog
""",
        encoding="utf-8",
    )
    return manifests, "present-task", "absent-task"


class TestHackTrajectoriesCli:
    def test_inventory_flags_present_and_absent(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        manifests, present_name, absent_name = _write_hack_fixture(tmp_path)
        monkeypatch.setattr(
            "verity_corpus.cli._registry", lambda: CorpusRegistry(manifests)
        )
        result = runner.invoke(app, ["hack-trajectories", "--inventory"])
        assert result.exit_code == 0, result.output
        assert "present" in result.output
        assert "absent" in result.output
        assert present_name in result.output
        assert absent_name in result.output
        assert "catalog-pointer" not in result.output
        assert "1 present / 1 absent / 2 total" in result.output

    def test_task_id_prints_absent_message(self, tmp_path: Path, monkeypatch) -> None:
        manifests, _, absent_name = _write_hack_fixture(tmp_path)
        monkeypatch.setattr(
            "verity_corpus.cli._registry", lambda: CorpusRegistry(manifests)
        )
        result = runner.invoke(app, ["hack-trajectories", absent_name])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == (
            f"no hack trajectories found for task {absent_name}"
        )

    def test_task_id_prints_recorded_exploit(self, tmp_path: Path, monkeypatch) -> None:
        manifests, present_name, _ = _write_hack_fixture(tmp_path)
        monkeypatch.setattr(
            "verity_corpus.cli._registry", lambda: CorpusRegistry(manifests)
        )
        result = runner.invoke(app, ["hack-trajectories", present_name])
        assert result.exit_code == 0, result.output
        assert "1 recorded exploit(s)" in result.output
        assert "v5: 1 action(s); rewarded (1)" in result.output

    def test_unknown_task_exits_nonzero(self, tmp_path: Path, monkeypatch) -> None:
        manifests, _, _ = _write_hack_fixture(tmp_path)
        monkeypatch.setattr(
            "verity_corpus.cli._registry", lambda: CorpusRegistry(manifests)
        )
        result = runner.invoke(app, ["hack-trajectories", "nope"])
        assert result.exit_code == 1
        assert "unknown task id 'nope'" in result.output
