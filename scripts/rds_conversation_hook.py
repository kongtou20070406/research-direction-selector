"""Advisory conversation hooks for direct calls to this installation's RDS CLI.

Input/output follow the Claude Code and Codex hook JSON contracts. This module
does not run commands, read transcripts, or write state. Hosts must configure a
short hook timeout. Complex shell programs deliberately receive no annotation.
"""
import json
from pathlib import Path
import re
import sys

MAX_INPUT_BYTES = 65536
MAX_COMMAND_CHARS = 16384
MAX_ARGS = 256
CLI_PATH = Path(__file__).resolve().with_name("rds_cli.py")
TOOLS = {"Bash", "bash", "PowerShell", "exec_command", "functions.exec_command", "shell_command"}
COMMANDS = frozenset("init hypothesis gate plan run data decide status project checkpoint artifacts formal meta history advise advancement branch usage exec reject guard hypergraph math rsi host-hook".split())
ROOT_OPTIONS = {"--root", "--workspace", "--project-root", "-w", "-d", "--dir"}

# Operator-visible status. Two hosts have a verified person-facing channel for
# it: Claude Code renders top-level systemMessage to the user, and Codex
# documents the same field as "surfaced as a warning in the UI or event stream"
# for SessionStart (docs/host-plugins.md carries the observation date and URL).
# OMP has no verified equivalent and stays silent. Until phase R implements the
# session switch there is no enable path in this checkout, so the truthful
# session state is "not enabled".
OPERATOR_STATUS = "RDS | session not enabled (default off); no RDS context injected"


def literal_argv(command):
    """Parse only plain words or whole quoted words, preserving Windows slashes.

    Shell expansions, operators, redirections and quoted concatenation are not
    supported, even when a particular shell would treat them as literal text.
    This is an observation filter, not a general shell parser or command guard.
    """
    if not isinstance(command, str) or not 0 < len(command) <= MAX_COMMAND_CHARS:
        return None
    if any(char in command for char in "\r\n\x00;$`|&<>(){}[]*?!#%"):
        return None
    args, i = [], 0
    while i < len(command):
        if command[i] in " \t":
            i += 1
            continue
        if command[i] in "\"'":
            quote = command[i]
            end = command.find(quote, i + 1)
            if end < 0 or end + 1 < len(command) and command[end + 1] not in " \t":
                return None
            word = command[i + 1:end]
            i = end + 1
        else:
            end = i
            while end < len(command) and command[end] not in " \t":
                end += 1
            word = command[i:end]
            if "'" in word or '"' in word:
                return None
            i = end
        if not word or len(args) >= MAX_ARGS:
            return None
        args.append(word)
    return args or None


def _local_path(value, cwd=None):
    if not isinstance(value, str) or not value or len(value) > MAX_COMMAND_CHARS:
        raise ValueError("Invalid path")
    # Never probe a network share just to render a status hint.
    if value.startswith(("\\\\", "//")) or "\x00" in value:
        raise ValueError("Unsupported path")
    path = Path(value)
    if not path.is_absolute():
        if cwd is None:
            raise ValueError("Absolute working directory required")
        path = cwd / path
    return path.resolve(strict=True)


def invocation(payload):
    """Return a fixed command label only for this installation's exact entry."""
    if payload.get("tool_name") not in TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    supplied = [tool_input[key] for key in ("command", "cmd", "argv") if key in tool_input]
    if len(supplied) != 1:
        return None
    raw = supplied[0]
    if isinstance(raw, list):
        if not 0 < len(raw) <= MAX_ARGS or any(
                not isinstance(arg, str) or not arg or "\x00" in arg for arg in raw):
            return None
        if sum(len(arg) for arg in raw) > MAX_COMMAND_CHARS:
            return None
        args = raw
    else:
        args = literal_argv(raw)
    if not args:
        return None
    interpreter = args[0].replace("\\", "/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?", interpreter, re.I):
        return None
    index = 1
    if interpreter.lower() in {"py", "py.exe"} and index < len(args) and re.fullmatch(r"-3(?:\.\d+)?", args[index]):
        index += 1
    while index < len(args) and args[index] in {"-B", "-u"}:
        index += 1
    if index >= len(args) or args[index].startswith("-"):
        return None
    cwd = _local_path(tool_input.get("workdir", tool_input.get("cwd", payload.get("cwd"))))
    if not cwd.is_dir() or _local_path(args[index], cwd) != CLI_PATH.resolve(strict=True):
        return None
    # Never echo paths, arbitrary arguments, prompts, command bodies or logs.
    tail = args[index + 1:]
    index = 0
    while index < len(tail):
        token = tail[index]
        if token in ROOT_OPTIONS:
            index += 2
            continue
        if any(token.startswith(option + "=") for option in ROOT_OPTIONS):
            index += 1
            continue
        if token in {"--version", "--help", "-h"}:
            return "CLI"
        return token if token in COMMANDS else "CLI"
    return "CLI"


def _result_status(payload):
    if payload["hook_event_name"] == "PostToolUseFailure":
        return "error"
    response = payload.get("tool_response")
    if not isinstance(response, dict):
        return "result unknown"
    if any(response.get(key) is True for key in ("isError", "is_error", "interrupted", "is_interrupt")):
        return "error"
    if isinstance(response.get("error"), str) and response["error"]:
        return "error"
    codes = [response[key] for key in ("exit_code", "exitCode") if key in response]
    if codes and all(type(code) is int for code in codes):
        return "returned" if all(code == 0 for code in codes) else "nonzero exit"
    return "result unknown"


def respond(payload, host):
    """Return event-specific additionalContext, never permissions or UI warnings.

    Session start deliberately contributes nothing. An unconditional banner is
    model context the operator cannot see and did not request, so it is removed
    until the session gate (phase R) can answer before any injection.
    """
    if host not in {"claude", "codex", "omp"} or not isinstance(payload, dict):
        return None
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        # Claude Code and Codex only. OMP has no verified operator-visible
        # channel, so it gets nothing rather than a guess. This return stays a
        # bare top-level object: Codex marks a PreToolUse hook run as failed
        # when it returns unsupported fields, and this module never emits
        # permissions or model context for a session-start event.
        if host not in {"claude", "codex"}:
            return None
        return {"systemMessage": OPERATOR_STATUS}
    elif event in {"PreToolUse", "PostToolUse"} or event == "PostToolUseFailure" and host == "claude":
        label = invocation(payload)
        if label is None:
            return None
        status = ("running" if host == "omp" else "pending") if event == "PreToolUse" else _result_status(payload)
        line = f"RDS | {label}: {status}"
        context = line + "\n"
        if event == "PreToolUse" and host == "omp":
            context += "phase=execution_started; display_once(observed_call_status); prefer_latest_tool_result;"
        elif event == "PreToolUse":
            context += "phase=before_execution; display_once(observed_call_status); do_not_infer(started);"
        else:
            context += "display_once(observed_call_status); call_returned != scientific_acceptance; call_returned != background_task_completed;"
    else:
        # SessionEnd has no model-context contract and there is nothing to clear.
        return None
    if host == "omp":
        return {"status": line, "additionalContext": context}
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "--host" or args[1] not in {"claude", "codex", "omp"}:
        return 0
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            return 0
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        output = respond(payload, args[1])
        if output is not None:
            sys.stdout.buffer.write((json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    except (ValueError, TypeError, OSError, UnicodeError, RecursionError):
        pass  # Advisory display failures never change the original operation.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
