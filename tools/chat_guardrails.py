"""Per-session guardrails for live chat tool use.

These are intentionally scoped by task_id/session_id instead of process-wide
environment variables so a Discord chat budget cannot accidentally constrain
cron jobs, Kanban workers, or another concurrent gateway turn.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import shlex
import threading
from typing import Any, Mapping


@dataclass(frozen=True)
class ChatToolPolicy:
    max_terminal_timeout: int | None = None
    max_tool_calls: int | None = None
    max_repeated_search_calls: int | None = 2
    block_broad_shell_search: bool = False
    broad_search_message: str = (
        "This looks like a broad shell search. Narrow the path/pattern, use rg "
        "with an explicit scoped directory, or create/claim a Kanban task for "
        "the deeper investigation instead of blocking the live Discord reply."
    )
    tool_budget_message: str = (
        "The live Discord tool budget is spent. Stop calling tools, summarize "
        "what you found in plain language, and create or update a Kanban task "
        "for any deeper work instead of blocking the chat."
    )
    repeated_search_message: str = (
        "This search is repeating without a new angle. Stop searching the same "
        "thing, explain what you know so far in plain language, and continue "
        "in a Kanban/background task if deeper investigation is needed."
    )


_POLICIES: dict[str, ChatToolPolicy] = {}
_TOOL_COUNTS: dict[str, int] = {}
_SEARCH_SIGNATURE_COUNTS: dict[str, dict[str, int]] = {}
_LOCK = threading.RLock()

_BROAD_GREP_RE = re.compile(r"\b(?:grep|egrep|fgrep)\b[^\n;&|]*\s-(?:[A-Za-z]*R|[A-Za-z]*r)")


def set_chat_tool_policy(task_id: str | None, policy: ChatToolPolicy | None) -> None:
    key = str(task_id or "").strip()
    if not key:
        return
    with _LOCK:
        if policy is None:
            _POLICIES.pop(key, None)
            _TOOL_COUNTS.pop(key, None)
            _SEARCH_SIGNATURE_COUNTS.pop(key, None)
        else:
            _POLICIES[key] = policy
            _TOOL_COUNTS[key] = 0
            _SEARCH_SIGNATURE_COUNTS[key] = {}


def clear_chat_tool_policy(task_id: str | None) -> None:
    set_chat_tool_policy(task_id, None)


def get_chat_tool_policy(task_id: str | None) -> ChatToolPolicy | None:
    key = str(task_id or "").strip()
    if not key:
        return None
    with _LOCK:
        return _POLICIES.get(key)


def _is_unscoped_rg(parts: list[str]) -> bool:
    if not parts or parts[0] != "rg":
        return False
    non_option_args: list[str] = []
    skip_next = False
    options_with_values = {
        "-g",
        "--glob",
        "-t",
        "--type",
        "-T",
        "--type-not",
        "-e",
        "--regexp",
        "-f",
        "--file",
        "--iglob",
        "--max-count",
        "-m",
        "--context",
        "-C",
        "--after-context",
        "-A",
        "--before-context",
        "-B",
    }
    for part in parts[1:]:
        if skip_next:
            skip_next = False
            continue
        if part in options_with_values:
            skip_next = True
            continue
        if part.startswith("-"):
            continue
        non_option_args.append(part)
    # rg PATTERN with no path searches the whole cwd. rg PATTERN PATH is scoped.
    return len(non_option_args) <= 1


def is_broad_shell_search(command: str) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    if _BROAD_GREP_RE.search(text):
        return True
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    if not parts:
        return False
    if _is_unscoped_rg(parts):
        return True
    if parts[:2] == ["find", "."]:
        return True
    return False


def _normalize_path(value: Any) -> str:
    text = str(value or ".").strip() or "."
    return re.sub(r"/+", "/", text.rstrip("/") or ".")


def _search_signature(function_name: str, args: Mapping[str, Any]) -> str | None:
    if function_name == "search_files":
        pattern = str(args.get("pattern") or args.get("query") or "").strip().lower()
        target = str(args.get("target") or "content").strip().lower()
        path = _normalize_path(args.get("path") or ".").lower()
        file_glob = str(args.get("file_glob") or "").strip().lower()
        if not pattern:
            return None
        return f"search_files:{target}:{path}:{file_glob}:{pattern}"

    if function_name != "terminal":
        return None
    command = str(args.get("command") or "").strip()
    if not command:
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return None
    tool = parts[0]
    if tool not in {"rg", "grep", "egrep", "fgrep"}:
        return None
    normalized_parts: list[str] = []
    skip_next = False
    for part in parts:
        if skip_next:
            normalized_parts.append(part.lower())
            skip_next = False
            continue
        if part in {"--max-count", "-m", "--context", "-C", "--after-context", "-A", "--before-context", "-B"}:
            skip_next = True
            continue
        if part.startswith("--json") or part.startswith("--color") or part.startswith("--line-number") or part in {"-n", "-H"}:
            continue
        normalized_parts.append(part.lower())
    return "terminal_search:" + " ".join(normalized_parts)


def apply_chat_tool_policy(
    function_name: str,
    function_args: Mapping[str, Any] | None,
    *,
    task_id: str | None,
) -> tuple[dict[str, Any], str | None]:
    args = dict(function_args or {})
    policy = get_chat_tool_policy(task_id)
    if policy is None:
        return args, None

    diagnostic: dict[str, Any] = {
        "task_id": task_id,
        "function_name": function_name,
        "has_policy": True,
        "max_tool_calls": policy.max_tool_calls,
        "max_terminal_timeout": policy.max_terminal_timeout,
        "max_repeated_search_calls": policy.max_repeated_search_calls,
        "block_broad_shell_search": policy.block_broad_shell_search,
    }

    search_signature = _search_signature(function_name, args)
    if search_signature and policy.max_repeated_search_calls is not None:
        key = str(task_id or "").strip()
        limit = max(1, int(policy.max_repeated_search_calls))
        with _LOCK:
            per_task = _SEARCH_SIGNATURE_COUNTS.setdefault(key, {})
            count = int(per_task.get(search_signature, 0))
            if count >= limit:
                diagnostic.update(
                    {
                        "allowed": False,
                        "reason": "repeated_search",
                        "search_signature": search_signature[:500],
                        "count_before": count,
                        "limit": limit,
                    }
                )
                try:
                    from gateway.biff_diagnostics import record_biff_diagnostic
                    record_biff_diagnostic("tool_policy", diagnostic)
                except Exception:
                    pass
                return args, policy.repeated_search_message
            per_task[search_signature] = count + 1
            diagnostic.update({"search_signature": search_signature[:500], "search_count_after": count + 1})

    if policy.max_tool_calls is not None:
        key = str(task_id or "").strip()
        limit = max(0, int(policy.max_tool_calls))
        with _LOCK:
            count = int(_TOOL_COUNTS.get(key, 0))
            if count >= limit:
                diagnostic.update({"allowed": False, "reason": "tool_budget_spent", "count_before": count, "limit": limit})
                try:
                    from gateway.biff_diagnostics import record_biff_diagnostic
                    record_biff_diagnostic("tool_policy", diagnostic)
                except Exception:
                    pass
                return args, policy.tool_budget_message
            _TOOL_COUNTS[key] = count + 1
            diagnostic.update({"count_before": count, "count_after": count + 1, "limit": limit})

    if function_name != "terminal":
        diagnostic.update({"allowed": True})
        try:
            from gateway.biff_diagnostics import record_biff_diagnostic
            record_biff_diagnostic("tool_policy", diagnostic)
        except Exception:
            pass
        return args, None

    command = str(args.get("command") or "")
    diagnostic["command"] = command[:500]
    if policy.block_broad_shell_search and is_broad_shell_search(command):
        diagnostic.update({"allowed": False, "reason": "broad_shell_search"})
        try:
            from gateway.biff_diagnostics import record_biff_diagnostic
            record_biff_diagnostic("tool_policy", diagnostic)
        except Exception:
            pass
        return args, policy.broad_search_message

    if policy.max_terminal_timeout:
        current = args.get("timeout")
        try:
            current_int = int(current) if current is not None else None
        except Exception:
            current_int = None
        cap = max(1, int(policy.max_terminal_timeout))
        if current_int is None or current_int > cap:
            args["timeout"] = cap
            diagnostic.update({"terminal_timeout_before": current_int, "terminal_timeout_after": cap})

    diagnostic.update({"allowed": True})
    try:
        from gateway.biff_diagnostics import record_biff_diagnostic
        record_biff_diagnostic("tool_policy", diagnostic)
    except Exception:
        pass
    return args, None
