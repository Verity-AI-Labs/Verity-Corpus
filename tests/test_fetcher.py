"""Tests for fetching local sources and git cache short-circuiting."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from verity_corpus.fetcher import FetchError, fetch, is_fetched
from verity_corpus.models.manifest import DomainTag, ManifestEntry, SourceSpec


def _local_entry(path: Path) -> ManifestEntry:
    return ManifestEntry.create(
        name="local",
        source=SourceSpec(type="local", path=str(path)),
        domain=DomainTag(category="other"),
        adapter="terminal",
    )


def _git_entry(commit: str | None = "abc123") -> ManifestEntry:
    return ManifestEntry.create(
        name="git-env",
        source=SourceSpec(
            type="git",
            url="https://github.com/example/env",
            commit=commit,
            path="tasks/foo",
        ),
        domain=DomainTag(category="terminal"),
        adapter="terminal",
    )


class TestFetchLocal:
    def test_returns_existing_path(self, tmp_path: Path) -> None:
        env_root = tmp_path / "env"
        env_root.mkdir()
        entry = _local_entry(env_root)
        assert fetch(entry, cache_dir=tmp_path / "cache") == env_root
        assert is_fetched(entry, cache_dir=tmp_path / "cache") is True

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        entry = _local_entry(tmp_path / "missing")
        assert is_fetched(entry) is False
        with pytest.raises(FetchError, match="does not exist"):
            fetch(entry, cache_dir=tmp_path / "cache")


class TestFetchGit:
    def test_is_fetched_false_before_true_after_marker(self, tmp_path: Path) -> None:
        entry = _git_entry()
        cache_dir = tmp_path / "cache"
        assert is_fetched(entry, cache_dir=cache_dir) is False
        target = cache_dir / entry.id
        (target / "tasks" / "foo").mkdir(parents=True)
        (target / ".verity_commit").write_text("abc123\n", encoding="utf-8")
        assert is_fetched(entry, cache_dir=cache_dir) is True

    def test_skips_clone_when_commit_matches(self, tmp_path: Path) -> None:
        entry = _git_entry()
        cache_dir = tmp_path / "cache"
        target = cache_dir / entry.id
        env_root = target / "tasks" / "foo"
        env_root.mkdir(parents=True)
        (target / ".verity_commit").write_text("abc123\n", encoding="utf-8")
        with patch("verity_corpus.fetcher.subprocess.run") as run:
            result = fetch(entry, cache_dir=cache_dir)
        run.assert_not_called()
        assert result == env_root.resolve()

    def test_clone_and_checkout_when_missing(self, tmp_path: Path) -> None:
        entry = _git_entry(commit="deadbeef")
        cache_dir = tmp_path / "cache"

        def fake_run(args, **kwargs):
            target = Path(args[-1]) if args[1] == "clone" else Path(kwargs.get("cwd") or ".")
            if args[1] == "clone":
                (target / entry.source.path).mkdir(parents=True)
                return _ok("cloned")
            if args[1] == "rev-parse":
                return _ok("deadbeef\n")
            return _ok("")

        with patch("verity_corpus.fetcher.subprocess.run", side_effect=fake_run) as run:
            result = fetch(entry, cache_dir=cache_dir)

        commands = [call.args[0][1] for call in run.call_args_list]
        assert commands[0] == "clone"
        assert "fetch" in commands
        assert "checkout" in commands
        assert (cache_dir / entry.id / ".verity_commit").read_text(encoding="utf-8").strip() == "deadbeef"
        assert result == (cache_dir / entry.id / "tasks" / "foo").resolve()
        assert is_fetched(entry, cache_dir=cache_dir) is True

    def test_git_failure_raises_fetch_error(self, tmp_path: Path) -> None:
        entry = _git_entry()
        with patch(
            "verity_corpus.fetcher.subprocess.run",
            return_value=_ok("", returncode=1, stderr="permission denied"),
        ):
            with pytest.raises(FetchError, match="permission denied"):
                fetch(entry, cache_dir=tmp_path / "cache")


def _ok(stdout: str, *, returncode: int = 0, stderr: str = ""):
    from types import SimpleNamespace

    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
