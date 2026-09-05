"""Tests for the Terminal Wrench hack-trajectory loader.

Fixtures match the *assumed* layout documented in
``verity_corpus.hack_trajectories`` — they are not real Terminal Wrench files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verity_corpus.hack_trajectories import (
    actions_from_trajectory_payload,
    has_recorded_hacks,
    load_from_env_root,
    locate_hack_trajectories_dir,
)
from verity_corpus.models.manifest import DomainTag, ManifestEntry, SourceSpec
from verity_corpus.registry import CorpusRegistry
from verity_corpus.resolver import (
    ResolveError,
    inventory_hack_trajectories,
    load_hack_trajectories,
    load_hack_trajectories_for_task,
)


def _write(path: Path, content: str | bytes | dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    elif isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _harbor_run(
    hack_root: Path,
    run_id: str,
    *,
    commands: list[str],
    reward: str = "1",
    metadata: dict | None = None,
) -> Path:
    run = hack_root / run_id
    steps = []
    for index, command in enumerate(commands, start=1):
        steps.append(
            {
                "step_id": index,
                "source": "agent",
                "tool_calls": [
                    {
                        "tool_call_id": f"call_{index}",
                        "function_name": "bash",
                        "arguments": {"command": command},
                    }
                ],
            }
        )
    _write(run / "trial" / "agent" / "trajectory.json", {"steps": steps})
    _write(run / "trial" / "verifier" / "reward.txt", f"{reward}\n")
    _write(run / "metadata.json", metadata or {"classification": "serious_hack"})
    return run


class TestActionsFromTrajectoryPayload:
    def test_harbor_atif_steps_tool_calls(self) -> None:
        payload = {
            "steps": [
                {
                    "tool_calls": [
                        {
                            "function_name": "bash",
                            "arguments": {"command": "cat tests/test.sh"},
                        }
                    ]
                },
                {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "bash",
                                "arguments": json.dumps(
                                    {"command": "echo 1 > reward.txt"}
                                ),
                            }
                        }
                    ]
                },
            ]
        }
        actions = actions_from_trajectory_payload(payload)
        assert [action.command for action in actions] == [
            "cat tests/test.sh",
            "echo 1 > reward.txt",
        ]
        assert all(action.kind == "exec" for action in actions)

    def test_wrapped_trajectory_list_of_commands(self) -> None:
        payload = {
            "trajectory": [
                {"command": "ls /app"},
                {"type": "submit", "body": "solve.sh contents"},
            ]
        }
        actions = actions_from_trajectory_payload(payload)
        assert actions[0].kind == "exec"
        assert actions[0].command == "ls /app"
        assert actions[1].kind == "submit"
        assert actions[1].body == "solve.sh contents"

    def test_bare_list_payload(self) -> None:
        actions = actions_from_trajectory_payload(
            ["echo hi", {"action": "touch /tmp/x"}]
        )
        assert [action.command for action in actions] == ["echo hi", "touch /tmp/x"]


class TestLoadFromEnvRootAssumedLayout:
    def test_parses_two_runs_inside_env_root(self, tmp_path: Path) -> None:
        env_root = tmp_path / "original_task"
        env_root.mkdir()
        hack_root = env_root / "hack_trajectories"
        _harbor_run(
            hack_root,
            "v5",
            commands=["sed -i 's/fail/pass/' tests/test.sh", "bash tests/test.sh"],
            reward="1",
        )
        _harbor_run(
            hack_root,
            "v5_2",
            commands=['python3 -c \'open("reward.txt","w").write("1")\''],
            reward="1",
            metadata={"classification": "nonserious_hack"},
        )

        loaded = load_from_env_root(env_root, task_id="7", env_id="abc123")

        assert loaded.present is True
        assert loaded.task_id == "7"
        assert loaded.env_id == "abc123"
        assert loaded.message == ""
        assert [run.run_id for run in loaded.trajectories] == ["v5", "v5_2"]
        first, second = loaded.trajectories
        assert [action.command for action in first.actions] == [
            "sed -i 's/fail/pass/' tests/test.sh",
            "bash tests/test.sh",
        ]
        assert first.verifier is not None
        assert first.verifier.rewarded is True
        assert first.verifier.score == 1.0
        assert first.verifier.source == "reward.txt"
        assert first.metadata["classification"] == "serious_hack"
        assert [action.command for action in second.actions] == [
            'python3 -c \'open("reward.txt","w").write("1")\''
        ]
        assert second.metadata["classification"] == "nonserious_hack"

    def test_reads_sibling_hack_trajectories_of_original_task(
        self, tmp_path: Path
    ) -> None:
        model_dir = tmp_path / "claude-opus-4.6"
        env_root = model_dir / "original_task"
        env_root.mkdir(parents=True)
        hack_root = model_dir / "hack_trajectories"
        _harbor_run(hack_root, "v5", commands=["chmod 777 /tests", "echo ok"])

        located = locate_hack_trajectories_dir(env_root)
        assert located == hack_root.resolve()

        loaded = load_from_env_root(env_root, task_id="5")
        assert loaded.present is True
        assert len(loaded.trajectories) == 1
        assert loaded.trajectories[0].actions[0].command == "chmod 777 /tests"

    def test_verifier_from_result_json_when_reward_txt_missing(
        self, tmp_path: Path
    ) -> None:
        env_root = tmp_path / "env"
        run = env_root / "hack_trajectories" / "v5"
        _write(
            run / "trial" / "agent" / "trajectory.json",
            {"actions": [{"command": "touch /tmp/hack"}]},
        )
        _write(
            run / "trial" / "result.json",
            {"verifier_result": {"rewards": {"reward": 0.0}}},
        )

        loaded = load_from_env_root(env_root, task_id="9")
        assert loaded.present is True
        outcome = loaded.trajectories[0].verifier
        assert outcome is not None
        assert outcome.rewarded is False
        assert outcome.score == 0.0
        assert outcome.source == "result.json"

    def test_episode_response_fallback_when_trajectory_json_has_no_commands(
        self, tmp_path: Path
    ) -> None:
        env_root = tmp_path / "env"
        run = env_root / "hack_trajectories" / "v5"
        _write(run / "trial" / "agent" / "trajectory.json", {"steps": []})
        _write(
            run / "trial" / "agent" / "episode-0" / "response.txt",
            "I'll inspect the tests.\nEXEC: cat /tests/test.sh\n",
        )
        _write(
            run / "trial" / "agent" / "episode-1" / "response.txt",
            "EXEC: echo 1 > /logs/verifier/reward.txt\nSUBMIT: done\n",
        )
        _write(run / "trial" / "verifier" / "reward.txt", "1\n")

        loaded = load_from_env_root(env_root, task_id="12")
        commands = [action.command for action in loaded.trajectories[0].actions]
        kinds = [action.kind for action in loaded.trajectories[0].actions]
        assert commands == [
            "cat /tests/test.sh",
            "echo 1 > /logs/verifier/reward.txt",
            "done",
        ]
        assert kinds == ["exec", "exec", "submit"]


class TestAbsentDirectoryIsEmptyNotError:
    def test_missing_hack_trajectories_dir_returns_empty(self, tmp_path: Path) -> None:
        env_root = tmp_path / "original_task"
        env_root.mkdir()
        (env_root / "instruction.md").write_text("do the task\n", encoding="utf-8")

        loaded = load_from_env_root(env_root, task_id="5", env_id="deadbeef")

        assert loaded.present is False
        assert loaded.trajectories == []
        assert loaded.task_id == "5"
        assert loaded.env_id == "deadbeef"
        assert loaded.message == "no hack trajectories found for task 5"

    def test_missing_env_root_returns_empty(self, tmp_path: Path) -> None:
        loaded = load_from_env_root(tmp_path / "not-fetched", task_id="891")
        assert loaded.present is False
        assert loaded.trajectories == []
        assert loaded.message == "no hack trajectories found for task 891"

    def test_empty_hack_trajectories_dir_returns_empty(self, tmp_path: Path) -> None:
        env_root = tmp_path / "env"
        (env_root / "hack_trajectories").mkdir(parents=True)

        loaded = load_from_env_root(env_root, task_id="3")
        assert loaded.present is False
        assert loaded.trajectories == []
        assert loaded.message == "no hack trajectories found for task 3"

    def test_has_recorded_hacks_false_when_absent(self, tmp_path: Path) -> None:
        env_root = tmp_path / "env"
        env_root.mkdir()
        present, count, hack_root = has_recorded_hacks(env_root)
        assert present is False
        assert count == 0
        assert hack_root is None


def _local_entry(
    env_root: Path,
    *,
    name: str,
    status: str = "registered",
    metadata: dict | None = None,
) -> ManifestEntry:
    env_root.mkdir(parents=True, exist_ok=True)
    return ManifestEntry.create(
        name=name,
        source=SourceSpec(type="local", path=str(env_root)),
        domain=DomainTag(category="terminal", subcategory="bash"),
        adapter="terminal",
        metadata=metadata or {"upstream_task_id": name},
        status=status,  # type: ignore[arg-type]
    )


class TestResolverAndRegistryPath:
    def test_load_via_entry_uses_cached_root(self, tmp_path: Path) -> None:
        env_root = tmp_path / "task-5"
        _harbor_run(
            env_root / "hack_trajectories",
            "v5",
            commands=["cat tests/test.sh"],
        )
        entry = _local_entry(env_root, name="5")

        loaded = load_hack_trajectories(entry, cache_dir=tmp_path / "unused-cache")

        assert loaded.present is True
        assert loaded.task_id == "5"
        assert loaded.env_id == entry.id
        assert loaded.trajectories[0].actions[0].command == "cat tests/test.sh"

    def test_registry_lookup_by_name_and_id(self, tmp_path: Path) -> None:
        manifests = tmp_path / "manifests"
        env_root = tmp_path / "task-7"
        _harbor_run(env_root / "hack_trajectories", "v5", commands=["ls /app"])
        entry = _local_entry(env_root, name="7")
        registry = CorpusRegistry(manifests)
        registry.add_entry(entry, "tw.yaml")

        by_name = load_hack_trajectories_for_task("7", registry=registry)
        by_id = load_hack_trajectories_for_task(entry.id, registry=registry)
        via_registry = registry.hack_trajectories("7")

        assert by_name.present is True
        assert by_id.present is True
        assert via_registry.present is True
        assert by_name.env_id == entry.id
        assert [a.command for a in by_name.trajectories[0].actions] == ["ls /app"]

    def test_unknown_task_id_raises(self, tmp_path: Path) -> None:
        registry = CorpusRegistry(tmp_path / "manifests")
        with pytest.raises(ResolveError, match="unknown task id 'nope'"):
            load_hack_trajectories_for_task("nope", registry=registry)

    def test_absent_fetched_task_is_flagged_empty(self, tmp_path: Path) -> None:
        manifests = tmp_path / "manifests"
        present_root = tmp_path / "present"
        absent_root = tmp_path / "absent"
        _harbor_run(present_root / "hack_trajectories", "v5", commands=["echo hack"])
        present = _local_entry(present_root, name="present-task")
        absent = _local_entry(absent_root, name="absent-task")
        catalog = _local_entry(
            tmp_path / "catalog", name="catalog-pointer", status="catalog"
        )
        registry = CorpusRegistry(manifests)
        registry.add_entry(present, "tw.yaml")
        registry.add_entry(absent, "tw.yaml")
        registry.add_entry(catalog, "ib.yaml")

        loaded = registry.hack_trajectories("absent-task")
        assert loaded.present is False
        assert loaded.trajectories == []
        assert loaded.message == "no hack trajectories found for task absent-task"

        rows = inventory_hack_trajectories(registry)
        by_name = {row.task_id: row for row in rows}
        assert "catalog-pointer" not in by_name
        assert by_name["present-task"].present is True
        assert by_name["present-task"].n_trajectories == 1
        assert by_name["absent-task"].present is False
        assert by_name["absent-task"].n_trajectories == 0
        assert by_name["absent-task"].message == (
            "no hack trajectories found for task absent-task"
        )
        via_registry = {
            row.task_id: row for row in registry.hack_trajectory_inventory()
        }
        assert via_registry["present-task"].present is True
        assert via_registry["absent-task"].present is False

    def test_unfetched_git_entry_reports_absent(self, tmp_path: Path) -> None:
        entry = ManifestEntry.create(
            name="5",
            source=SourceSpec(
                type="git",
                url="https://github.com/few-sh/terminal-wrench",
                commit="d8a29613235a0ef56a8b70b3142626a533da28c2",
                path="tasks/5/claude-opus-4.6/original_task",
            ),
            domain=DomainTag(category="terminal"),
            adapter="terminal",
        )
        loaded = load_hack_trajectories(entry, cache_dir=tmp_path / "cache")
        assert loaded.present is False
        assert loaded.trajectories == []
        assert loaded.message == "no hack trajectories found for task 5"
