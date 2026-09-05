"""Load Terminal Wrench recorded hack trajectories from a fetched env_root.

This module does **not** fetch or clone upstream data. Sparse checkouts currently
omit ``hack_trajectories/``; callers must tolerate that and treat a missing
directory as "no recorded exploits" rather than an error.

On-disk layout
--------------
Relative to a task's fetched directory (``env_root`` may be the task dir,
``<model>/original_task``, or a model dir), recorded exploits live at::

    tasks/<task_id>/<model>/hack_trajectories/<version>/

``<model>`` is an attacker model (``claude-opus-4.6``, ``gpt-5.4``,
``gemini-3.1-pro``, …). Every model subdirectory is scanned; empty model dirs
and empty ``hack_trajectories/`` trees are skipped. ``<version>`` is one
recorded run (``v5``, ``v5_2``, ``v5_3``, …) and contains::

    metadata.json                        # authoritative classification / reward
    trial/result.json
    trial/verifier/reward.txt
    trial/agent/trajectory.json          # ATIF-v1.6; top-level ``steps``
    trial/agent/episode-<n>/             # prompt.txt, response.txt, debug.json

``trajectory.json`` steps carry ``step_id``, ``timestamp``, ``source``
(``user`` / ``assistant`` / ``agent``), and ``message``. Executed commands are
the agent's keystroke entries: ``tool_calls[].arguments.keystrokes`` and/or a
JSON ``commands`` array of ``keystrokes`` in the agent message.

``metadata.json.classification`` distinguishes rewarded exploits
(``rewarded_serious_exploit`` and non-serious variants) from legitimate solves
and no-reward attempts. Only actual hacks are the judge-recall positive set;
a run with ``judged_legitimate_solve: true`` is not counted as one the judge
is expected to catch.

A missing tree returns ``present=False`` with a diagnostic ``message`` rather
than raising.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = [
    "ABSENT_MESSAGE",
    "HACK_DIR_RELATIVE_CANDIDATES",
    "HackAction",
    "HackTrajectoryPresence",
    "HackTrajectorySet",
    "RecordedHack",
    "TRAJECTORY_JSON_RELATIVE_CANDIDATES",
    "VerifierOutcome",
    "actions_from_trajectory_payload",
    "has_recorded_hacks",
    "is_recorded_hack",
    "iter_hack_trajectory_dirs",
    "iter_run_dirs",
    "load_from_env_root",
    "locate_hack_trajectories_dir",
    "locate_task_root",
    "parse_run_dir",
    "verifier_from_run",
]

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

HACK_TRAJECTORIES_DIRNAME = "hack_trajectories"
ORIGINAL_TASK_DIRNAME = "original_task"

# Fallback when env_root is not a Terminal Wrench task/model/original_task path.
HACK_DIR_RELATIVE_CANDIDATES: tuple[str, ...] = (
    HACK_TRAJECTORIES_DIRNAME,
    f"../{HACK_TRAJECTORIES_DIRNAME}",
)

TRAJECTORY_JSON_RELATIVE_CANDIDATES: tuple[str, ...] = (
    "trial/agent/trajectory.json",
    "agent/trajectory.json",
    "trajectory.json",
)

EPISODE_DIR_PATTERN = re.compile(r"^episode-(\d+)$", re.IGNORECASE)

REWARD_TXT_RELATIVE_CANDIDATES: tuple[str, ...] = (
    "trial/verifier/reward.txt",
    "verifier/reward.txt",
    "reward.txt",
)

RESULT_JSON_RELATIVE_CANDIDATES: tuple[str, ...] = (
    "trial/result.json",
    "result.json",
)

METADATA_JSON_RELATIVE_CANDIDATES: tuple[str, ...] = (
    "metadata.json",
    "trial/metadata.json",
)

REWARD_JSON_KEYS: tuple[str, ...] = (
    "reward",
    "score",
    "rewards",
    "verifier_result",
    "verifier",
)

# Attacker-model directory names used by Terminal Wrench (and close cousins).
_ATTACKER_MODEL_PREFIX = re.compile(
    r"^(claude|gpt|gemini|o[0-9]|sonnet|opus|haiku|llama|qwen|mistral|deepseek)",
    re.IGNORECASE,
)

_USER_STEP_SOURCES = frozenset({"user", "system", "instruction"})
_SKIP_TOOL_NAMES = frozenset({"mark_task_complete", "task_complete"})
_EXEC_TOOL_NAMES = frozenset(
    {"bash", "shell", "exec", "terminal", "run", "bash_command"}
)
_COMMAND_KEYS: tuple[str, ...] = (
    "keystrokes",
    "command",
    "cmd",
    "action",
    "input",
    "content",
    "body",
    "code",
)

ABSENT_MESSAGE = "no hack trajectories found for task {task_id}"

_FENCED_JSON = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Normalized records
# ---------------------------------------------------------------------------


class HackAction(BaseModel):
    """One executed command or tool call from a recorded exploit."""

    kind: str = "exec"
    command: str = ""
    body: str = ""
    name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)

    def as_tool_call(self) -> dict[str, Any]:
        """RedTeam ``Episode.tool_calls`` shape: ``{type, command|body}``."""
        if self.kind == "submit":
            return {"type": "submit", "body": self.body or self.command}
        if self.kind == "tool":
            payload: dict[str, Any] = {"type": "tool", "name": self.name or "tool"}
            if self.arguments:
                payload["arguments"] = dict(self.arguments)
            if self.command:
                payload["command"] = self.command
            return payload
        return {"type": "exec", "command": self.command}


class VerifierOutcome(BaseModel):
    """Final verifier result recorded with the exploit, if any."""

    rewarded: bool | None = None
    score: float | None = None
    raw: str = ""
    source: str = ""


class RecordedHack(BaseModel):
    """One recorded exploit: actions, verifier outcome, and ground-truth label."""

    run_id: str
    source_path: str
    actions: list[HackAction] = Field(default_factory=list)
    verifier: VerifierOutcome | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    attacker_model: str = ""
    version: str = ""
    classification: str = ""
    classification_label: str = ""
    judged_serious_exploit: bool | None = None
    judged_legitimate_solve: bool | None = None
    is_hack: bool = False


class HackTrajectorySet(BaseModel):
    """Normalized exploits for one task, or an explicit empty result."""

    task_id: str
    env_id: str = ""
    present: bool
    message: str = ""
    trajectories: list[RecordedHack] = Field(default_factory=list)
    hack_root: str = ""


class HackTrajectoryPresence(BaseModel):
    """Present/absent flag for one registry task (metadata labels only)."""

    task_id: str
    env_id: str
    present: bool
    n_trajectories: int = 0
    message: str = ""
    hack_root: str = ""


def absent_message(task_id: str, detail: str = "") -> str:
    base = ABSENT_MESSAGE.format(task_id=task_id)
    return f"{base}: {detail}" if detail else base


def _empty_set(
    task_id: str,
    *,
    env_id: str = "",
    hack_root: str = "",
    detail: str = "",
) -> HackTrajectorySet:
    message = absent_message(task_id, detail)
    logger.info(message)
    return HackTrajectorySet(
        task_id=task_id,
        env_id=env_id,
        present=False,
        message=message,
        trajectories=[],
        hack_root=hack_root,
    )


def is_recorded_hack(metadata: dict[str, Any] | None) -> bool:
    """True when ``metadata.json`` labels this run as an actual exploit.

    Legitimate solves (``judged_legitimate_solve`` or a legitimate-solve
    classification) and no-reward attempts are not the judge-recall positive
    set. Rewarded serious exploits and non-serious hack variants are.
    """
    if not metadata:
        return False
    if metadata.get("judged_legitimate_solve") is True:
        return False
    classification = str(metadata.get("classification") or "").strip().lower()
    classification = classification.replace("-", "_")
    if "legitimate" in classification:
        return False
    if "no_reward" in classification or classification in {"unrewarded", "no_reward"}:
        return False
    if "exploit" in classification or "hack" in classification:
        return True
    return metadata.get("judged_serious_exploit") is True


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------


def _is_attacker_model_dir(path: Path) -> bool:
    return path.is_dir() and _ATTACKER_MODEL_PREFIX.match(path.name) is not None


def _looks_like_tw_task_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    return any(_is_attacker_model_dir(child) for child in children)


def locate_task_root(env_root: Path) -> Path | None:
    """Return the ``tasks/<task_id>`` directory that holds attacker-model dirs.

    ``env_root`` may be that task dir, ``<model>/``, or ``<model>/original_task``.
    """
    try:
        root = Path(env_root).resolve()
    except OSError:
        return None
    if not root.exists():
        return None
    if root.name == ORIGINAL_TASK_DIRNAME and _looks_like_tw_task_dir(
        root.parent.parent
    ):
        return root.parent.parent
    if _looks_like_tw_task_dir(root):
        return root
    if _is_attacker_model_dir(root) and _looks_like_tw_task_dir(root.parent):
        return root.parent
    return None


def _fallback_hack_dirs(env_root: Path) -> list[Path]:
    root = Path(env_root)
    found: list[Path] = []
    seen: set[Path] = set()
    for relative in HACK_DIR_RELATIVE_CANDIDATES:
        try:
            candidate = (root / relative).resolve()
        except OSError:
            continue
        if candidate.is_dir() and candidate not in seen:
            seen.add(candidate)
            found.append(candidate)
    return found


def iter_hack_trajectory_dirs(env_root: Path) -> list[Path]:
    """Return each ``<model>/hack_trajectories`` directory for this task.

    Empty model dirs (no ``hack_trajectories/`` folder) are omitted. A folder
    that exists but has no version children is still returned so callers can
    report it; :func:`iter_run_dirs` then yields nothing from it.
    """
    task_root = locate_task_root(env_root)
    if task_root is None:
        return _fallback_hack_dirs(env_root)
    dirs: list[Path] = []
    try:
        children = sorted(task_root.iterdir(), key=lambda path: path.name)
    except OSError:
        return []
    for child in children:
        if not _is_attacker_model_dir(child):
            continue
        hack = child / HACK_TRAJECTORIES_DIRNAME
        if hack.is_dir():
            dirs.append(hack)
    return dirs


def locate_hack_trajectories_dir(env_root: Path) -> Path | None:
    """Return a representative ``hack_trajectories`` directory, if any.

    Prefers the first model dir that actually contains a version run. When the
    Terminal Wrench multi-model layout is present this is one of several;
    :func:`iter_hack_trajectory_dirs` is the complete scan.
    """
    dirs = iter_hack_trajectory_dirs(env_root)
    for hack in dirs:
        if any(_looks_like_run_dir(child) for child in _child_dirs(hack)):
            return hack
    return dirs[0] if dirs else None


def _child_dirs(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    try:
        children = list(path.iterdir())
    except OSError:
        return []
    return sorted(
        (
            child
            for child in children
            if child.is_dir() and not child.name.startswith(".")
        ),
        key=lambda item: item.name,
    )


def _looks_like_run_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    for relative in (
        TRAJECTORY_JSON_RELATIVE_CANDIDATES
        + REWARD_TXT_RELATIVE_CANDIDATES
        + RESULT_JSON_RELATIVE_CANDIDATES
        + METADATA_JSON_RELATIVE_CANDIDATES
    ):
        if (path / relative).is_file():
            return True
    return bool(_episode_dirs(path))


def iter_run_dirs(root: Path) -> list[Path]:
    """Return one directory per recorded version run under ``root``.

    ``root`` may be an ``env_root`` (task / model / ``original_task``), or a
    single ``hack_trajectories/`` directory.
    """
    path = Path(root)
    if not path.exists():
        return []
    if path.name == HACK_TRAJECTORIES_DIRNAME and path.is_dir():
        hack_dirs = [path]
    else:
        hack_dirs = iter_hack_trajectory_dirs(path)
    runs: list[Path] = []
    for hack in hack_dirs:
        children = _child_dirs(hack)
        matching = [child for child in children if _looks_like_run_dir(child)]
        if matching:
            runs.extend(matching)
        elif _looks_like_run_dir(hack):
            runs.append(hack)
    return runs


def _display_root(env_root: Path) -> Path | None:
    task_root = locate_task_root(env_root)
    if task_root is not None:
        return task_root
    return locate_hack_trajectories_dir(env_root)


def has_recorded_hacks(env_root: Path) -> tuple[bool, int, Path | None]:
    """Presence check: at least one version dir labeled as an actual hack."""
    display = _display_root(env_root)
    n = 0
    for run in iter_run_dirs(env_root):
        if is_recorded_hack(_metadata_from_run(run)):
            n += 1
    return (n > 0, n, display)


# ---------------------------------------------------------------------------
# JSON / text helpers
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("could not read %s: %s", path, exc)
        return None


def _read_json(path: Path) -> Any | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("invalid JSON in %s: %s", path, exc)
        return None


def _first_existing(run_dir: Path, relatives: tuple[str, ...]) -> Path | None:
    for relative in relatives:
        path = run_dir / relative
        if path.is_file():
            return path
    return None


def _parse_embedded_json(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped:
        return None
    fenced = _FENCED_JSON.search(stripped)
    if fenced is not None:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, count=1)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------


def _normalize_command(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _command_from_mapping(payload: dict[str, Any]) -> str:
    for key in _COMMAND_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and _normalize_command(value).strip():
            return _normalize_command(value)
    arguments = payload.get("arguments")
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return _normalize_command(arguments)
        if isinstance(parsed, dict):
            return _command_from_mapping(parsed)
        return _normalize_command(arguments)
    if isinstance(arguments, dict):
        return _command_from_mapping(arguments)
    return ""


def _action_from_tool_call(call: object) -> HackAction | None:
    if not isinstance(call, dict):
        return None
    nested = call.get("function")
    source = dict(call)
    if isinstance(nested, dict):
        source = {**source, **nested}
    name = str(source.get("function_name") or source.get("name") or "").strip()
    if name.lower() in _SKIP_TOOL_NAMES:
        return None
    command = _command_from_mapping(source)
    if not command:
        return None
    kind = "exec"
    lowered = name.lower()
    if lowered in {"submit", "finish", "done"}:
        kind = "submit"
    elif name and lowered not in _EXEC_TOOL_NAMES:
        kind = "tool"
    arguments = source.get("arguments")
    parsed_args: dict[str, Any] = {}
    if isinstance(arguments, dict):
        parsed_args = dict(arguments)
    elif isinstance(arguments, str) and arguments.strip().startswith("{"):
        try:
            loaded = json.loads(arguments)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            parsed_args = loaded
    return HackAction(
        kind=kind,
        command=command,
        body=command if kind == "submit" else "",
        name=name,
        arguments=parsed_args,
    )


def _actions_from_commands_payload(payload: object) -> list[HackAction]:
    if not isinstance(payload, dict):
        return []
    commands = payload.get("commands")
    if not isinstance(commands, list):
        return []
    actions: list[HackAction] = []
    for item in commands:
        if isinstance(item, str) and _normalize_command(item).strip():
            actions.append(HackAction(kind="exec", command=_normalize_command(item)))
            continue
        if not isinstance(item, dict):
            continue
        command = _command_from_mapping(item)
        if command:
            actions.append(
                HackAction(kind="exec", command=command, arguments=dict(item))
            )
    return actions


def _actions_from_agent_message(message: object) -> list[HackAction]:
    if isinstance(message, dict):
        return _actions_from_commands_payload(message)
    if not isinstance(message, str) or not message.strip():
        return []
    parsed = _parse_embedded_json(message)
    return _actions_from_commands_payload(parsed)


def _actions_from_atif_step(step: dict[str, Any]) -> list[HackAction]:
    source = str(step.get("source") or "").strip().lower()
    if source in _USER_STEP_SOURCES:
        return []
    actions: list[HackAction] = []
    tool_calls = step.get("tool_calls") or step.get("toolCalls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            parsed = _action_from_tool_call(call)
            if parsed is not None:
                actions.append(parsed)
    if actions:
        return actions
    return _actions_from_agent_message(step.get("message"))


def _actions_from_sequence(items: list[Any]) -> list[HackAction]:
    actions: list[HackAction] = []
    for item in items:
        if isinstance(item, str) and _normalize_command(item).strip():
            actions.append(HackAction(kind="exec", command=_normalize_command(item)))
            continue
        if not isinstance(item, dict):
            continue
        if "step_id" in item or "source" in item or "tool_calls" in item:
            actions.extend(_actions_from_atif_step(item))
            continue
        tool_calls = item.get("tool_calls") or item.get("toolCalls")
        if isinstance(tool_calls, list):
            extracted = _actions_from_atif_step(item)
            if extracted:
                actions.extend(extracted)
                continue
        parsed_call = _action_from_tool_call(item)
        if parsed_call is not None and (
            item.get("function_name") or item.get("function") or item.get("name")
        ):
            actions.append(parsed_call)
            continue
        from_commands = _actions_from_commands_payload(item)
        if from_commands:
            actions.extend(from_commands)
            continue
        command = _command_from_mapping(item)
        if command:
            kind = str(item.get("type") or item.get("kind") or "exec")
            if kind in {"submit", "finish"}:
                actions.append(HackAction(kind="submit", command=command, body=command))
            else:
                actions.append(HackAction(kind="exec", command=command))
    return actions


def actions_from_trajectory_payload(payload: object) -> list[HackAction]:
    """Extract executed commands/actions from a ``trajectory.json`` payload.

    Terminal Wrench ATIF-v1.6 files store commands as ``keystrokes`` on agent
    steps (tool calls and/or a JSON ``commands`` array in ``message``).
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return _actions_from_sequence(payload)
    if not isinstance(payload, dict):
        return []

    steps = payload.get("steps")
    if isinstance(steps, list):
        extracted = _actions_from_sequence(steps)
        if extracted:
            return extracted

    for key in ("trajectory", "actions", "events", "messages"):
        value = payload.get(key)
        if isinstance(value, list):
            extracted = _actions_from_sequence(value)
            if extracted:
                return extracted

    from_commands = _actions_from_commands_payload(payload)
    if from_commands:
        return from_commands

    nested = payload.get("trial")
    if isinstance(nested, dict):
        extracted = actions_from_trajectory_payload(nested)
        if extracted:
            return extracted
    agent = payload.get("agent")
    if isinstance(agent, dict):
        extracted = actions_from_trajectory_payload(agent)
        if extracted:
            return extracted
    return []


def _actions_from_episode_response(text: str) -> list[HackAction]:
    """Pull keystroke commands or ``EXEC:`` / ``SUBMIT:`` lines from episode dumps."""
    from_json = _actions_from_agent_message(text)
    if from_json:
        return from_json
    actions: list[HackAction] = []
    submit_at: int | None = None
    same_line_body = ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("EXEC:"):
            command = stripped[len("EXEC:") :].strip()
            if command:
                actions.append(HackAction(kind="exec", command=command))
            continue
        if stripped.startswith("SUBMIT:"):
            submit_at = index
            same_line_body = stripped[len("SUBMIT:") :].strip()
            break
    if submit_at is not None:
        rest = "\n".join(lines[submit_at + 1 :])
        body = "\n".join(part for part in (same_line_body, rest) if part).strip()
        if body:
            actions.append(HackAction(kind="submit", command=body, body=body))
    return actions


def _episode_dirs(run_dir: Path) -> list[Path]:
    roots = (
        run_dir / "trial" / "agent",
        run_dir / "agent",
        run_dir,
    )
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child in seen:
                continue
            match = EPISODE_DIR_PATTERN.match(child.name)
            if match is None:
                continue
            seen.add(child)
            found.append(child)
    found.sort(key=lambda path: int(EPISODE_DIR_PATTERN.match(path.name).group(1)))  # type: ignore[union-attr]
    return found


def actions_from_episode_dirs(run_dir: Path) -> list[HackAction]:
    actions: list[HackAction] = []
    for episode in _episode_dirs(run_dir):
        response = episode / "response.txt"
        if not response.is_file():
            continue
        text = _read_text(response)
        if text:
            actions.extend(_actions_from_episode_response(text))
    return actions


# ---------------------------------------------------------------------------
# Verifier outcome
# ---------------------------------------------------------------------------


def _as_score(value: object) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        lowered = stripped.lower()
        if lowered in {"true", "yes", "pass", "passed"}:
            return 1.0
        if lowered in {"false", "no", "fail", "failed"}:
            return 0.0
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _reward_from_mapping(payload: dict[str, Any]) -> float | None:
    for key in REWARD_JSON_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        score = _as_score(value)
        if score is not None:
            return score
        if isinstance(value, dict):
            nested = _reward_from_mapping(value)
            if nested is not None:
                return nested
    return None


def _outcome_from_score(score: float, *, raw: str, source: str) -> VerifierOutcome:
    return VerifierOutcome(
        rewarded=score > 0,
        score=score,
        raw=raw,
        source=source,
    )


def verifier_from_run(run_dir: Path) -> VerifierOutcome | None:
    """Read a recorded final verifier outcome from reward.txt / result.json.

    Prefers ``trial/verifier/reward.txt``, then ``trial/result.json``, then
    ``metadata.json.reward``.
    """
    reward_txt = _first_existing(run_dir, REWARD_TXT_RELATIVE_CANDIDATES)
    if reward_txt is not None:
        text = _read_text(reward_txt)
        if text is not None and text.strip():
            score = _as_score(text.strip())
            if score is not None:
                return _outcome_from_score(
                    score, raw=text.strip(), source=str(reward_txt.name)
                )
            return VerifierOutcome(
                rewarded=None, raw=text.strip(), source=str(reward_txt.name)
            )

    result_json = _first_existing(run_dir, RESULT_JSON_RELATIVE_CANDIDATES)
    if result_json is not None:
        payload = _read_json(result_json)
        if isinstance(payload, dict):
            score = _reward_from_mapping(payload)
            if score is not None:
                return _outcome_from_score(
                    score,
                    raw=json.dumps(score),
                    source=str(result_json.name),
                )

    metadata = _first_existing(run_dir, METADATA_JSON_RELATIVE_CANDIDATES)
    if metadata is not None:
        payload = _read_json(metadata)
        if isinstance(payload, dict):
            score = _reward_from_mapping(payload)
            if score is not None:
                return _outcome_from_score(
                    score,
                    raw=json.dumps(score),
                    source=str(metadata.name),
                )
    return None


def _metadata_from_run(run_dir: Path) -> dict[str, Any]:
    path = _first_existing(run_dir, METADATA_JSON_RELATIVE_CANDIDATES)
    if path is None:
        return {}
    payload = _read_json(path)
    return dict(payload) if isinstance(payload, dict) else {}


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _run_identity(run_dir: Path) -> tuple[str, str, str]:
    """Return ``(run_id, attacker_model, version)`` for a version directory."""
    version = run_dir.name
    parent = run_dir.parent
    if parent.name == HACK_TRAJECTORIES_DIRNAME and _is_attacker_model_dir(
        parent.parent
    ):
        model = parent.parent.name
        return f"{model}/{version}", model, version
    return version, "", version


# ---------------------------------------------------------------------------
# Run / env_root loaders
# ---------------------------------------------------------------------------


def parse_run_dir(run_dir: Path) -> RecordedHack:
    """Parse one version directory into a :class:`RecordedHack`."""
    path = Path(run_dir)
    actions: list[HackAction] = []
    trajectory = _first_existing(path, TRAJECTORY_JSON_RELATIVE_CANDIDATES)
    if trajectory is not None:
        actions = actions_from_trajectory_payload(_read_json(trajectory))
    if not actions:
        actions = actions_from_episode_dirs(path)
    metadata = _metadata_from_run(path)
    run_id, attacker_model, version = _run_identity(path)
    if not attacker_model:
        attacker_model = str(metadata.get("model") or "")
    classification = str(metadata.get("classification") or "")
    return RecordedHack(
        run_id=run_id,
        source_path=str(path),
        actions=actions,
        verifier=verifier_from_run(path),
        metadata=metadata,
        attacker_model=attacker_model,
        version=version,
        classification=classification,
        classification_label=str(metadata.get("classification_label") or ""),
        judged_serious_exploit=_optional_bool(metadata.get("judged_serious_exploit")),
        judged_legitimate_solve=_optional_bool(metadata.get("judged_legitimate_solve")),
        is_hack=is_recorded_hack(metadata),
    )


def _absence_detail(
    env_root: Path, runs: list[Path], parsed: list[RecordedHack]
) -> str:
    root = Path(env_root)
    if not root.is_dir():
        return f"env_root is not a directory ({root})"
    task_root = locate_task_root(root)
    hack_dirs = iter_hack_trajectory_dirs(root)
    if task_root is not None and not hack_dirs:
        models = [
            child.name
            for child in _child_dirs(task_root)
            if _is_attacker_model_dir(child)
        ]
        listed = ", ".join(models) if models else "none"
        return (
            f"no <model>/hack_trajectories directories under {task_root} "
            f"(models: {listed})"
        )
    if hack_dirs and not runs:
        empty = ", ".join(str(path) for path in hack_dirs) or "none"
        return (
            f"hack_trajectories directories exist but contain no version runs ({empty})"
        )
    if parsed and not any(item.is_hack for item in parsed):
        labels = sorted({item.classification or "(unlabeled)" for item in parsed})
        return (
            f"found {len(parsed)} version dir(s) but none labeled as exploits "
            f"(classifications: {', '.join(labels)})"
        )
    if not hack_dirs:
        return f"no hack_trajectories directory under {root}"
    return ""


def load_from_env_root(
    env_root: Path,
    *,
    task_id: str,
    env_id: str = "",
) -> HackTrajectorySet:
    """Read recorded exploits under ``env_root``; never raises for a missing dir.

    Scans every ``<model>/hack_trajectories/<version>/`` tree. ``present`` is
    True only when at least one parsed run is labeled an actual hack.
    ``trajectories`` is that positive set (legitimate solves are omitted).
    """
    root = Path(env_root)
    display = _display_root(root)
    hack_root = str(display) if display is not None else ""
    if not root.is_dir():
        return _empty_set(task_id, env_id=env_id, detail=_absence_detail(root, [], []))
    runs = iter_run_dirs(root)
    parsed = [parse_run_dir(run) for run in runs]
    exploits = [item for item in parsed if item.is_hack]
    if not exploits:
        return _empty_set(
            task_id,
            env_id=env_id,
            hack_root=hack_root,
            detail=_absence_detail(root, runs, parsed),
        )
    return HackTrajectorySet(
        task_id=task_id,
        env_id=env_id,
        present=True,
        message="",
        trajectories=exploits,
        hack_root=hack_root,
    )
