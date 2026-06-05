"""Routing helpers for Biff's non-blocking Discord chat lane."""

from __future__ import annotations

from typing import Any

from agent.biff_intent_router import route_biff_live_intent


def should_use_biff_parallel_chat_lane(
    text: Any,
    *,
    platform_key: str | None,
    command: bool = False,
    running_agent: bool = True,
) -> tuple[bool, str]:
    """Return whether a busy Discord session should answer in a side lane."""

    if not running_agent or command:
        return False, "not an ordinary busy chat message"
    if str(platform_key or "").strip().lower() != "discord":
        return False, "parallel chat lane is Discord-only"
    route = route_biff_live_intent(text, command=False)
    if route.action in {"answer_now", "one_tool", "quick_web"}:
        return True, f"{route.action}: {route.reason}"
    return False, f"{route.action}: keep with main work lane"
