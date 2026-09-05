"""Load Terminal Wrench recorded hack trajectories from a fetched env_root.

This module does **not** fetch or clone upstream data. Sparse checkouts currently
omit ``hack_trajectories/``; callers must tolerate that and treat a missing
directory as "no recorded exploits" rather than an error.

Assumed on-disk layout
----------------------
The real files are not in the local checkout, so path and JSON key choices live
**only** in the constants and parse helpers below. Correct them in this file
once the upstream data is present.

Relative to ``env_root`` (the fetched task directory RedTeam already resolves),
the first existing candidate wins::

    {env_root}/hack_trajectories/
    {env_root}/../hack_trajectories/     # sibling of original_task (TW README)

Each child directory of ``hack_trajectories/`` is one recorded exploit
(``v5``, ``v5_2``, …). A run is assumed to look like::

    {run_id}/
      metadata.json                      # optional classification / extras
      trial/
        agent/
          trajectory.json                # primary action log
          episode-{N}/                   # optional Harbor episode dumps
            prompt.txt
            response.txt
        result.json                      # optional Harbor result
        verifier/
          reward.txt                     # "0" / "1" or a float
          test-stdout.txt

If ``trial/`` is missing, the same filenames are accepted directly under
``{run_id}/``. If ``hack_trajectories/`` itself holds ``trajectory.json`` (or
``trial/``) with no child run dirs, it is treated as a single run.

``trajectory.json`` action extraction (see :func:`actions_from_trajectory_payload`)
tries Harbor ATIF ``steps[].tool_calls``, then wrapped ``trajectory`` /
``actions`` lists, then OpenAI-style ``messages``. Episode ``response.txt``
files are a fallback when the JSON yields no commands.
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
    "iter_run_dirs",
    "load_from_env_root",
    "locate_hack_trajectories_dir",
    "parse_run_dir",
    "verifier_from_run",
]

# ---------------------------------------------------------------------------
# Layout — the only place to edit when real Terminal Wrench files are on disk.
# ---------------------------------------------------------------------------

HACK_TRAJECTORIES_DIRNAME = "hack_trajectories"

# Paths are relative to env_root. "../hack_trajectories" is the Terminal Wrench
# README layout: env_root is ``.../<model>/original_task`` and recorded hacks
# live next to that directory, not inside it.
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

# Keys tried (in order) when pulling a numeric/bool reward out of JSON.
REWARD_JSON_KEYS: tuple[str, ...] = (
    "reward",
    "score",
    "rewards",
    "verifier_result",
    "verifier",
)

ABSENT_MESSAGE = "no hack trajectories found for task {task_id}"


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
    """One recorded exploit: actions plus optional verifier outcome."""

    run_id: str
    source_path: str
    actions: list[HackAction] = Field(default_factory=list)
    verifier: VerifierOutcome | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HackTrajectorySet(BaseModel):
    """Normalized exploits for one task, or an explicit empty result."""

    task_id: str
    env_id: str = ""
    present: bool
    message: str = ""
    trajectories: list[RecordedHack] = Field(default_factory=list)
    hack_root: str = ""


class HackTrajectoryPresence(BaseModel):
    """Present/absent flag for one registry task (no full parse)."""

    task_id: str
    env_id: str
    present: bool
    n_trajectories: int = 0
    message: str = ""
    hack_root: str = ""


def absent_message(task_id: str) -> str:
    return ABSENT_MESSAGE.format(task_id=task_id)


def _empty_set(
    task_id: str, *, env_id: str = "", hack_root: str = ""
) -> HackTrajectorySet:
    message = absent_message(task_id)
    logger.info(message)
    return HackTrajectorySet(
        task_id=task_id,
        env_id=env_id,
        present=False,
        message=message,
        trajectories=[],
        hack_root=hack_root,
    )


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------


def locate_hack_trajectories_dir(env_root: Path) -> Path | None:
    """Return the ``hack_trajectories`` directory under ``env_root``, if any.

    Candidates are :data:`HACK_DIR_RELATIVE_CANDIDATES`. First existing
    directory wins.
    """
    root = Path(env_root)
    for relative in HACK_DIR_RELATIVE_CANDIDATES:
        candidate = (root / relative).resolve()
        if candidate.is_dir():
            return candidate
    return None


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


def iter_run_dirs(hack_root: Path) -> list[Path]:
    """Return one directory per recorded exploit under ``hack_root``.

    Child directories that look like runs are preferred. If none qualify and
    ``hack_root`` itself looks like a single run, return ``[hack_root]``.
    """
    root = Path(hack_root)
    if not root.is_dir():
        return []
    children = sorted(
        (
            child
            for child in root.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        ),
        key=lambda path: path.name,
    )
    runs = [child for child in children if _looks_like_run_dir(child)]
    if runs:
        return runs
    if _looks_like_run_dir(root):
        return [root]
    return []


def has_recorded_hacks(env_root: Path) -> tuple[bool, int, Path | None]:
    """Cheap presence check: directory exists and contains at least one run."""
    hack_root = locate_hack_trajectories_dir(env_root)
    if hack_root is None:
        return False, 0, None
    runs = iter_run_dirs(hack_root)
    return (bool(runs), len(runs), hack_root)


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


# ---------------------------------------------------------------------------
# Action parsing — isolated so key names are trivial to correct.
# ---------------------------------------------------------------------------


def _command_from_mapping(payload: dict[str, Any]) -> str:
    for key in ("command", "cmd", "action", "input", "content", "body", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    arguments = payload.get("arguments")
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments.strip()
        if isinstance(parsed, dict):
            return _command_from_mapping(parsed)
        return arguments.strip()
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
    command = _command_from_mapping(source)
    if not command and not name:
        return None
    kind = "exec"
    lowered = name.lower()
    if lowered in {"submit", "finish", "done"}:
        kind = "submit"
    elif name and lowered not in {"bash", "shell", "exec", "terminal", "run"}:
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


def _actions_from_sequence(items: list[Any]) -> list[HackAction]:
    actions: list[HackAction] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            actions.append(HackAction(kind="exec", command=item.strip()))
            continue
        if not isinstance(item, dict):
            continue
        tool_calls = item.get("tool_calls") or item.get("toolCalls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                parsed = _action_from_tool_call(call)
                if parsed is not None:
                    actions.append(parsed)
            continue
        parsed_call = _action_from_tool_call(item)
        if parsed_call is not None and (
            item.get("function_name") or item.get("function") or item.get("name")
        ):
            actions.append(parsed_call)
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

    This is the only function that interprets trajectory JSON keys. Update it
    when real Terminal Wrench files disagree with the assumed Harbor/ATIF
    shapes below.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return _actions_from_sequence(payload)
    if not isinstance(payload, dict):
        return []

    for key in ("steps", "trajectory", "actions", "events", "messages"):
        value = payload.get(key)
        if isinstance(value, list):
            extracted = _actions_from_sequence(value)
            if extracted:
                return extracted

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
    """Pull ``EXEC:`` / ``SUBMIT:`` lines out of Harbor ``response.txt`` dumps."""
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
        for child in root.iterdir():
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
# Verifier outcome — isolated JSON/text keys.
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

    Key and filename choices are :data:`REWARD_TXT_RELATIVE_CANDIDATES`,
    :data:`RESULT_JSON_RELATIVE_CANDIDATES`, and :data:`REWARD_JSON_KEYS`.
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


# ---------------------------------------------------------------------------
# Run / env_root loaders
# ---------------------------------------------------------------------------


def parse_run_dir(run_dir: Path) -> RecordedHack:
    """Parse one assumed exploit directory into a :class:`RecordedHack`."""
    path = Path(run_dir)
    actions: list[HackAction] = []
    trajectory = _first_existing(path, TRAJECTORY_JSON_RELATIVE_CANDIDATES)
    if trajectory is not None:
        actions = actions_from_trajectory_payload(_read_json(trajectory))
    if not actions:
        actions = actions_from_episode_dirs(path)
    return RecordedHack(
        run_id=path.name,
        source_path=str(path),
        actions=actions,
        verifier=verifier_from_run(path),
        metadata=_metadata_from_run(path),
    )


def load_from_env_root(
    env_root: Path,
    *,
    task_id: str,
    env_id: str = "",
) -> HackTrajectorySet:
    """Read recorded exploits under ``env_root``; never raises for a missing dir.

    When ``hack_trajectories/`` is absent (the current sparse-checkout state)
    this returns ``present=False`` and message
    ``no hack trajectories found for task {task_id}``.
    """
    root = Path(env_root)
    if not root.is_dir():
        return _empty_set(task_id, env_id=env_id)
    hack_root = locate_hack_trajectories_dir(root)
    if hack_root is None:
        return _empty_set(task_id, env_id=env_id)
    runs = iter_run_dirs(hack_root)
    if not runs:
        return _empty_set(task_id, env_id=env_id, hack_root=str(hack_root))
    trajectories = [parse_run_dir(run) for run in runs]
    return HackTrajectorySet(
        task_id=task_id,
        env_id=env_id,
        present=True,
        message="",
        trajectories=trajectories,
        hack_root=str(hack_root),
    )
