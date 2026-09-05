"""Tests for the Terminal Wrench hack-trajectory loader.

Synthetic fixtures mirror the real on-disk layout::

    tasks/<task_id>/<model>/hack_trajectories/<version>/
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verity_corpus.hack_trajectories import (
    actions_from_trajectory_payload,
    has_recorded_hacks,
    is_recorded_hack,
    load_from_env_root,
    locate_task_root,
)
from verity_corpus.models.manifest import DomainTag, ManifestEntry, SourceSpec
from verity_corpus.registry import CorpusRegistry
from verity_corpus.resolver import (
    ResolveError,
    inventory_hack_trajectories,
    load_hack_trajectories,
    load_hack_trajectories_for_task,
)

REAL_TW_TASKS = (
    Path.home() / "verity-labs/Verity-RedTeam/cache/repos/97545567df177f9f/tasks"
)


def _write(path: Path, content: str | bytes | dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    elif isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _exploit_metadata(
    *,
    task_id: str = "7",
    model: str = "claude-opus-4.6",
    classification: str = "rewarded_serious_exploit",
    judged_serious: bool = True,
    judged_legitimate: bool = False,
    reward: float = 1.0,
) -> dict:
    return {
        "task_id": task_id,
        "model": model,
        "classification": classification,
        "classification_label": classification.replace("_", " "),
        "reward": reward,
        "hack_prompt_version": 5,
        "judged_serious_exploit": judged_serious,
        "judged_legitimate_solve": judged_legitimate,
        "episode_count": 1,
    }


def _atif_payload(commands: list[str], *, with_tool_calls: bool = True) -> dict:
    command_objs = [
        {"keystrokes": f"{command}\n", "duration": 0.1} for command in commands
    ]
    tool_calls = []
    for index, command in enumerate(commands):
        tool_calls.append(
            {
                "tool_call_id": f"call_{index}",
                "function_name": "bash_command",
                "arguments": {"keystrokes": f"{command}\n", "duration": 0.1},
            }
        )
    tool_calls.append(
        {
            "tool_call_id": "call_task_complete",
            "function_name": "mark_task_complete",
            "arguments": {},
        }
    )
    agent_step: dict = {
        "step_id": 2,
        "timestamp": "2026-04-09T20:47:32.049286+00:00",
        "source": "agent",
        "message": json.dumps(
            {
                "analysis": "bypass the verifier",
                "plan": "write expected files directly",
                "commands": command_objs,
                "task_complete": True,
            }
        ),
    }
    if with_tool_calls:
        agent_step["tool_calls"] = tool_calls
    return {
        "schema_version": "ATIF-v1.6",
        "steps": [
            {
                "step_id": 1,
                "timestamp": "2026-04-09T20:47:19.258003+00:00",
                "source": "user",
                "message": "Task description and JSON keystroke instructions.",
            },
            agent_step,
        ],
    }


def _atif_run(
    task_root: Path,
    model: str,
    version: str,
    *,
    commands: list[str],
    reward: str = "1",
    metadata: dict | None = None,
    with_tool_calls: bool = True,
) -> Path:
    run = task_root / model / "hack_trajectories" / version
    _write(
        run / "trial" / "agent" / "trajectory.json",
        _atif_payload(commands, with_tool_calls=with_tool_calls),
    )
    _write(run / "trial" / "verifier" / "reward.txt", f"{reward}\n")
    _write(
        run / "trial" / "result.json",
        {"verifier_result": {"reward": float(reward)}},
    )
    meta = metadata or _exploit_metadata(task_id=task_root.name, model=model)
    _write(run / "metadata.json", meta)
    original = task_root / model / "original_task"
    original.mkdir(parents=True, exist_ok=True)
    return run


def _tw_task(tmp_path: Path, task_id: str, *models: str) -> tuple[Path, Path]:
    """Create ``tasks/<id>/<model>/original_task`` for each model; return task, env_root."""
    task_root = tmp_path / "tasks" / task_id
    env_root = task_root / models[0] / "original_task"
    for model in models:
        (task_root / model / "original_task").mkdir(parents=True, exist_ok=True)
    return task_root, env_root


class TestActionsFromTrajectoryPayload:
    def test_atif_v16_keystrokes_from_tool_calls(self) -> None:
        payload = _atif_payload(
            ["cat tests/test.sh", "echo 1 > reward.txt"],
            with_tool_calls=True,
        )
        actions = actions_from_trajectory_payload(payload)
        assert [action.command for action in actions] == [
            "cat tests/test.sh",
            "echo 1 > reward.txt",
        ]
        assert all(action.kind == "exec" for action in actions)

    def test_atif_commands_array_when_tool_calls_missing(self) -> None:
        payload = _atif_payload(["printf 'x\\n' > /app/out.txt"], with_tool_calls=False)
        actions = actions_from_trajectory_payload(payload)
        assert [action.command for action in actions] == [
            "printf 'x\\n' > /app/out.txt"
        ]

    def test_skips_user_steps_and_mark_task_complete(self) -> None:
        payload = _atif_payload(["ls /app"])
        actions = actions_from_trajectory_payload(payload)
        assert [action.command for action in actions] == ["ls /app"]
        assert all(action.name != "mark_task_complete" for action in actions)

    def test_harbor_style_command_arguments_still_parse(self) -> None:
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


class TestIsRecordedHack:
    def test_rewarded_serious_exploit_is_a_hack(self) -> None:
        assert is_recorded_hack(_exploit_metadata()) is True

    def test_nonserious_variant_is_a_hack(self) -> None:
        assert (
            is_recorded_hack(
                _exploit_metadata(
                    classification="rewarded_nonserious_hack",
                    judged_serious=False,
                )
            )
            is True
        )

    def test_legitimate_solve_is_not_a_hack(self) -> None:
        assert (
            is_recorded_hack(
                _exploit_metadata(
                    classification="rewarded_legitimate_solve",
                    judged_serious=False,
                    judged_legitimate=True,
                )
            )
            is False
        )


class TestLoadFromEnvRootTwLayout:
    def test_parses_all_models_and_versions_from_original_task(
        self, tmp_path: Path
    ) -> None:
        task_root, env_root = _tw_task(
            tmp_path, "7", "claude-opus-4.6", "gpt-5.4", "gemini-3.1-pro"
        )
        _atif_run(
            task_root,
            "claude-opus-4.6",
            "v5",
            commands=["sed -i 's/fail/pass/' tests/test.sh", "bash tests/test.sh"],
        )
        _atif_run(
            task_root,
            "claude-opus-4.6",
            "v5_2",
            commands=['python3 -c \'open("reward.txt","w").write("1")\''],
            metadata=_exploit_metadata(
                task_id="7",
                model="claude-opus-4.6",
                classification="rewarded_nonserious_hack",
                judged_serious=False,
            ),
        )
        _atif_run(
            task_root,
            "gpt-5.4",
            "v5",
            commands=["chmod 777 /tests"],
            metadata=_exploit_metadata(task_id="7", model="gpt-5.4"),
        )
        (task_root / "gemini-3.1-pro" / "hack_trajectories").mkdir(parents=True)

        assert locate_task_root(env_root) == task_root.resolve()
        loaded = load_from_env_root(env_root, task_id="7", env_id="abc123")

        assert loaded.present is True
        assert loaded.task_id == "7"
        assert loaded.env_id == "abc123"
        assert loaded.message == ""
        assert loaded.hack_root == str(task_root.resolve())
        assert [run.run_id for run in loaded.trajectories] == [
            "claude-opus-4.6/v5",
            "claude-opus-4.6/v5_2",
            "gpt-5.4/v5",
        ]
        first, second, third = loaded.trajectories
        assert [action.command for action in first.actions] == [
            "sed -i 's/fail/pass/' tests/test.sh",
            "bash tests/test.sh",
        ]
        assert first.verifier is not None
        assert first.verifier.rewarded is True
        assert first.verifier.score == 1.0
        assert first.verifier.source == "reward.txt"
        assert first.classification == "rewarded_serious_exploit"
        assert first.judged_serious_exploit is True
        assert first.judged_legitimate_solve is False
        assert first.is_hack is True
        assert first.attacker_model == "claude-opus-4.6"
        assert second.classification == "rewarded_nonserious_hack"
        assert [action.command for action in third.actions] == ["chmod 777 /tests"]
        assert third.attacker_model == "gpt-5.4"

    def test_loads_from_the_task_directory_itself(self, tmp_path: Path) -> None:
        task_root, _env_root = _tw_task(tmp_path, "5", "claude-opus-4.6")
        _atif_run(task_root, "claude-opus-4.6", "v5", commands=["ls /app"])

        loaded = load_from_env_root(task_root, task_id="5")
        assert loaded.present is True
        assert len(loaded.trajectories) == 1
        assert loaded.trajectories[0].actions[0].command == "ls /app"

    def test_excludes_legitimate_solves_from_the_positive_set(
        self, tmp_path: Path
    ) -> None:
        task_root, env_root = _tw_task(tmp_path, "5", "claude-opus-4.6", "gpt-5.4")
        _atif_run(
            task_root,
            "claude-opus-4.6",
            "v5",
            commands=["echo 'First output' > /app/output1.txt"],
        )
        _atif_run(
            task_root,
            "gpt-5.4",
            "v5",
            commands=["echo 'First output' > /app/output1.txt"],
            metadata=_exploit_metadata(
                task_id="5",
                model="gpt-5.4",
                classification="rewarded_legitimate_solve",
                judged_serious=False,
                judged_legitimate=True,
            ),
        )

        loaded = load_from_env_root(env_root, task_id="5")
        assert loaded.present is True
        assert [run.run_id for run in loaded.trajectories] == ["claude-opus-4.6/v5"]
        assert loaded.trajectories[0].is_hack is True

    def test_only_legitimate_solves_is_absent(self, tmp_path: Path) -> None:
        task_root, env_root = _tw_task(tmp_path, "5", "claude-opus-4.6")
        _atif_run(
            task_root,
            "claude-opus-4.6",
            "v5",
            commands=["echo ok"],
            metadata=_exploit_metadata(
                judged_serious=False,
                judged_legitimate=True,
                classification="rewarded_legitimate_solve",
            ),
        )

        loaded = load_from_env_root(env_root, task_id="5")
        assert loaded.present is False
        assert loaded.trajectories == []
        assert "none labeled as exploits" in loaded.message
        assert loaded.message.startswith("no hack trajectories found for task 5")

    def test_verifier_from_result_json_when_reward_txt_missing(
        self, tmp_path: Path
    ) -> None:
        task_root, env_root = _tw_task(tmp_path, "9", "claude-opus-4.6")
        run = task_root / "claude-opus-4.6" / "hack_trajectories" / "v5"
        _write(
            run / "trial" / "agent" / "trajectory.json",
            {
                "schema_version": "ATIF-v1.6",
                "steps": [],
                "actions": [{"command": "touch /tmp/hack"}],
            },
        )
        _write(
            run / "trial" / "result.json",
            {"verifier_result": {"rewards": {"reward": 0.0}}},
        )
        _write(
            run / "metadata.json",
            _exploit_metadata(task_id="9", reward=0.0),
        )

        loaded = load_from_env_root(env_root, task_id="9")
        assert loaded.present is True
        outcome = loaded.trajectories[0].verifier
        assert outcome is not None
        assert outcome.rewarded is False
        assert outcome.score == 0.0
        assert outcome.source == "result.json"

    def test_episode_response_json_fallback_when_trajectory_has_no_commands(
        self, tmp_path: Path
    ) -> None:
        task_root, env_root = _tw_task(tmp_path, "12", "claude-opus-4.6")
        run = task_root / "claude-opus-4.6" / "hack_trajectories" / "v5"
        _write(
            run / "trial" / "agent" / "trajectory.json",
            {"schema_version": "ATIF-v1.6", "steps": []},
        )
        _write(
            run / "trial" / "agent" / "episode-0" / "response.txt",
            json.dumps(
                {
                    "analysis": "inspect",
                    "plan": "read tests",
                    "commands": [
                        {"keystrokes": "cat /tests/test.sh\n", "duration": 0.1}
                    ],
                }
            )
            + "\n",
        )
        _write(
            run / "trial" / "agent" / "episode-1" / "response.txt",
            "EXEC: echo 1 > /logs/verifier/reward.txt\nSUBMIT: done\n",
        )
        _write(run / "trial" / "verifier" / "reward.txt", "1\n")
        _write(run / "metadata.json", _exploit_metadata(task_id="12"))

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
        _task_root, env_root = _tw_task(tmp_path, "5", "claude-opus-4.6")
        (env_root / "instruction.md").write_text("do the task\n", encoding="utf-8")

        loaded = load_from_env_root(env_root, task_id="5", env_id="deadbeef")

        assert loaded.present is False
        assert loaded.trajectories == []
        assert loaded.task_id == "5"
        assert loaded.env_id == "deadbeef"
        assert loaded.message.startswith("no hack trajectories found for task 5")
        assert "no <model>/hack_trajectories" in loaded.message

    def test_missing_env_root_returns_empty(self, tmp_path: Path) -> None:
        loaded = load_from_env_root(tmp_path / "not-fetched", task_id="891")
        assert loaded.present is False
        assert loaded.trajectories == []
        assert "env_root is not a directory" in loaded.message

    def test_empty_hack_trajectories_dir_returns_empty(self, tmp_path: Path) -> None:
        task_root, env_root = _tw_task(tmp_path, "3", "claude-opus-4.6")
        (task_root / "claude-opus-4.6" / "hack_trajectories").mkdir(parents=True)

        loaded = load_from_env_root(env_root, task_id="3")
        assert loaded.present is False
        assert loaded.trajectories == []
        assert loaded.message.startswith("no hack trajectories found for task 3")
        assert "no version runs" in loaded.message

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
        task_root, env_root = _tw_task(tmp_path, "5", "claude-opus-4.6")
        _atif_run(task_root, "claude-opus-4.6", "v5", commands=["cat tests/test.sh"])
        entry = _local_entry(env_root, name="5")

        loaded = load_hack_trajectories(entry, cache_dir=tmp_path / "unused-cache")

        assert loaded.present is True
        assert loaded.task_id == "5"
        assert loaded.env_id == entry.id
        assert loaded.trajectories[0].actions[0].command == "cat tests/test.sh"
        assert loaded.trajectories[0].classification == "rewarded_serious_exploit"

    def test_registry_lookup_by_name_and_id(self, tmp_path: Path) -> None:
        manifests = tmp_path / "manifests"
        task_root, env_root = _tw_task(tmp_path, "7", "claude-opus-4.6")
        _atif_run(task_root, "claude-opus-4.6", "v5", commands=["ls /app"])
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
        present_task, present_root = _tw_task(
            tmp_path, "present-task", "claude-opus-4.6"
        )
        _absent_task, absent_root = _tw_task(tmp_path, "absent-task", "claude-opus-4.6")
        _atif_run(present_task, "claude-opus-4.6", "v5", commands=["echo hack"])
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
        assert loaded.message.startswith(
            "no hack trajectories found for task absent-task"
        )

        rows = inventory_hack_trajectories(registry)
        by_name = {row.task_id: row for row in rows}
        assert "catalog-pointer" not in by_name
        assert by_name["present-task"].present is True
        assert by_name["present-task"].n_trajectories == 1
        assert by_name["absent-task"].present is False
        assert by_name["absent-task"].n_trajectories == 0
        assert by_name["absent-task"].message.startswith(
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
        assert loaded.message.startswith("no hack trajectories found for task 5")


@pytest.mark.skipif(
    not (REAL_TW_TASKS / "5" / "claude-opus-4.6" / "hack_trajectories").is_dir(),
    reason="real Terminal Wrench hack_trajectories are not on disk",
)
class TestRealTerminalWrenchData:
    def test_task_5_from_task_dir_and_original_task(self) -> None:
        task_dir = REAL_TW_TASKS / "5"
        from_task = load_from_env_root(task_dir, task_id="5")
        from_original = load_from_env_root(
            task_dir / "claude-opus-4.6" / "original_task", task_id="5"
        )
        for loaded in (from_task, from_original):
            assert loaded.present is True
            assert len(loaded.trajectories) > 0
            first = loaded.trajectories[0]
            assert first.actions
            assert all(action.command for action in first.actions)
            assert first.verifier is not None
            assert first.verifier.score is not None
            assert first.classification
            assert first.is_hack is True
            assert first.judged_legitimate_solve is not True
        assert {run.run_id for run in from_task.trajectories} == {
            run.run_id for run in from_original.trajectories
        }
        models = {run.attacker_model for run in from_task.trajectories}
        assert "claude-opus-4.6" in models
        assert len(models) > 1

    def test_load_hack_trajectories_for_task_5_via_registry(self) -> None:
        cache_dir = REAL_TW_TASKS.parent.parent.parent
        loaded = load_hack_trajectories_for_task("5", cache_dir=cache_dir)
        assert loaded.present is True
        assert len(loaded.trajectories) > 0
        assert loaded.trajectories[0].classification
        assert loaded.trajectories[0].actions

    @pytest.mark.parametrize("task_id", ["5", "550", "regex-chess"])
    def test_known_tasks_have_parsed_exploits(self, task_id: str) -> None:
        loaded = load_from_env_root(REAL_TW_TASKS / task_id, task_id=task_id)
        assert loaded.present is True, loaded.message
        assert len(loaded.trajectories) > 0
        assert all(run.is_hack for run in loaded.trajectories)
        assert all(run.classification for run in loaded.trajectories)
        assert all(run.actions for run in loaded.trajectories)
