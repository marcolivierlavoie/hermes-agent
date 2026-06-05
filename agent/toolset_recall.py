"""Bounded toolset recall-on-miss helpers for Biff selective tool routing.

The gateway can start Discord turns with a narrow tool schema. If a model still
attempts a tool that is within the platform-configured ceiling but missing from
this turn's narrow surface, these helpers widen the live agent once and record a
metric so the turn can recover instead of ending with a raw "unknown tool".
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SAFE_RECALL_TOOLSETS: frozenset[str] = frozenset(
    {
        "web",
        "search",
        "browser",
        "terminal",
        "file",
        "kanban",
        "memory",
        "session_search",
        "vision",
    }
)

# A requested tool may need a small support surface to be usable. Keep this
# intentionally narrow; no specialist/delegation/cron/admin expansion here.
SUPPORT_TOOLSETS_BY_TOOLSET: dict[str, frozenset[str]] = {
    "web": frozenset({"web", "search", "browser", "terminal", "file"}),
    "search": frozenset({"web", "search", "browser", "terminal", "file"}),
    "browser": frozenset({"web", "search", "browser", "terminal", "file"}),
    "terminal": frozenset({"terminal", "file"}),
    "file": frozenset({"terminal", "file"}),
    "kanban": frozenset({"kanban", "terminal"}),
    "memory": frozenset({"memory", "session_search", "terminal"}),
    "session_search": frozenset({"memory", "session_search", "terminal"}),
    "vision": frozenset({"vision", "file", "terminal"}),
}


def _tool_name_from_call(tool_call: Any) -> str:
    try:
        return str(tool_call.function.name or "").strip()
    except Exception:
        return ""


def _toolset_for_tool(tool_name: str) -> str | None:
    if not tool_name:
        return None
    try:
        from model_tools import get_toolset_for_tool

        toolset = get_toolset_for_tool(tool_name)
        return str(toolset).strip() or None
    except Exception:
        return None


def _sorted_unique(values: Any) -> list[str]:
    return sorted(dict.fromkeys(str(v).strip() for v in (values or []) if str(v).strip()))


def recall_toolsets_for_missing_tool(
    tool_name: str,
    *,
    current_toolsets: Any,
    ceiling_toolsets: Any,
    disabled_toolsets: Any = None,
) -> tuple[list[str], dict[str, Any] | None]:
    """Return a widened toolset list for a safe missing tool, or no event.

    The ceiling is the platform-configured toolset list captured before the
    selective Biff router narrowed the turn. The helper never grants anything
    outside that ceiling, and it only handles low-risk operator/retrieval
    toolsets. Specialist delegation and high-side-effect integrations stay out
    of recall-on-miss by design.
    """

    current = set(_sorted_unique(current_toolsets))
    ceiling = set(_sorted_unique(ceiling_toolsets or current_toolsets))
    disabled = set(_sorted_unique(disabled_toolsets))
    source_toolset = _toolset_for_tool(tool_name)
    if source_toolset not in SAFE_RECALL_TOOLSETS:
        return _sorted_unique(current), None
    wanted = set(SUPPORT_TOOLSETS_BY_TOOLSET.get(source_toolset, frozenset({source_toolset})))
    wanted = {toolset for toolset in wanted if toolset in SAFE_RECALL_TOOLSETS}
    grantable = wanted & ceiling
    grantable -= disabled
    if not grantable:
        return _sorted_unique(current), None
    if grantable <= current:
        return _sorted_unique(current), None
    selected = _sorted_unique(current | grantable)
    event = {
        "kind": "toolset_recall_on_miss",
        "missing_tool": tool_name,
        "source_toolset": source_toolset,
        "added_toolsets": _sorted_unique(grantable - current),
        "selected_toolsets": selected,
    }
    return selected, event


def widen_agent_tools_for_missing_tool(agent: Any, tool_name: str) -> dict[str, Any] | None:
    """Widen ``agent.tools`` once for a safe missing tool and record metrics.

    Returns an event dict when widening happened. Returns None when recall is
    not applicable or the bounded retry was already consumed.
    """

    if str(getattr(agent, "platform", "") or "").strip().lower() != "discord":
        return None
    attempts = int(getattr(agent, "_toolset_recall_attempts", 0) or 0)
    max_attempts = int(getattr(agent, "_toolset_recall_max_attempts", 1) or 1)
    if attempts >= max_attempts:
        return None

    current_toolsets = _sorted_unique(getattr(agent, "enabled_toolsets", None))
    ceiling_toolsets = _sorted_unique(
        getattr(agent, "_toolset_recall_ceiling", None) or current_toolsets
    )
    disabled_toolsets = _sorted_unique(getattr(agent, "disabled_toolsets", None))
    widened_toolsets, event = recall_toolsets_for_missing_tool(
        tool_name,
        current_toolsets=current_toolsets,
        ceiling_toolsets=ceiling_toolsets,
        disabled_toolsets=disabled_toolsets,
    )
    if not event:
        return None

    try:
        from model_tools import get_tool_definitions

        new_tools = get_tool_definitions(
            enabled_toolsets=widened_toolsets,
            disabled_toolsets=disabled_toolsets,
            quiet_mode=True,
        )
    except Exception as exc:
        logger.warning(
            "biff_toolset_recall_failed: missing_tool=%s selected_toolsets=%s error=%s",
            tool_name,
            widened_toolsets,
            exc,
        )
        return None

    new_names = {
        tool.get("function", {}).get("name")
        for tool in new_tools
        if isinstance(tool, dict)
    }
    if tool_name not in new_names:
        return None

    agent.tools = list(new_tools)
    agent.valid_tool_names = {name for name in new_names if name}
    agent.enabled_toolsets = widened_toolsets
    agent._toolset_recall_attempts = attempts + 1
    recall_events = getattr(agent, "_toolset_recall_events", None)
    if not isinstance(recall_events, list):
        recall_events = []
        agent._toolset_recall_events = recall_events
    recall_events.append(event)
    logger.info(
        "biff_toolset_recall_on_miss: missing_tool=%s source_toolset=%s added_toolsets=%s selected_toolsets=%s",
        tool_name,
        event["source_toolset"],
        event["added_toolsets"],
        event["selected_toolsets"],
    )
    return event


__all__ = [
    "SAFE_RECALL_TOOLSETS",
    "recall_toolsets_for_missing_tool",
    "widen_agent_tools_for_missing_tool",
]
