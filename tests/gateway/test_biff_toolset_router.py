"""Unit tests for gateway.biff_toolset_router — pure logic, no gateway machinery.

Tests cover:
  - biff_toolset_router_enabled()  — every flag resolution path
  - route_class_for_plan()         — action/runtime/profile classification
  - select_biff_toolsets_with_router() — route application, ceiling, fallback
  - BiffToolsetRouteDecision.to_dict()
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.biff_toolset_router import (
    BiffToolsetRouteDecision,
    biff_toolset_router_enabled,
    route_class_for_plan,
    select_biff_toolsets_with_router,
)

# ── Enabled / disabled ──────────────────────────────────────────────


def test_router_disabled_by_default(monkeypatch):
    """No env var + no platform config → disabled."""
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    assert biff_toolset_router_enabled({"biff": {"platforms": {"discord": {}}}}, "discord") is False


def test_router_disabled_when_no_biff_platform_config(monkeypatch):
    """No biff key at all → disabled."""
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    assert biff_toolset_router_enabled({}, "discord") is False


def test_router_disabled_for_none_platform(monkeypatch):
    """Non-Discord / None platforms stay disabled regardless of flag."""
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    # Even with a positive config, non-Discord is off
    config = {"biff": {"platforms": {"telegram": {"toolset_router": True}}}}
    assert biff_toolset_router_enabled(config, "telegram") is False


def test_router_disabled_for_unconfigured_platform(monkeypatch):
    """Platform key that has no platform config entry → disabled."""
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    assert biff_toolset_router_enabled({}, "discord") is False


def test_router_disabled_by_env_0(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "0")
    assert biff_toolset_router_enabled({}, "discord") is False


def test_router_disabled_by_env_false(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "false")
    assert biff_toolset_router_enabled({}, "discord") is False


def test_router_disabled_by_env_off(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "OFF")
    assert biff_toolset_router_enabled({}, "discord") is False


def test_router_disabled_by_env_disabled(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "disabled")
    assert biff_toolset_router_enabled({}, "discord") is False


def test_router_disabled_by_env_no(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "no")
    assert biff_toolset_router_enabled({}, "discord") is False


def test_router_enabled_by_env_1(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "1")
    assert biff_toolset_router_enabled({}, "discord") is True


def test_router_enabled_by_env_true(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "true")
    assert biff_toolset_router_enabled({}, "discord") is True


def test_router_enabled_by_env_on(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "ON")
    assert biff_toolset_router_enabled({}, "discord") is True


def test_router_enabled_by_env_enabled(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "enabled")
    assert biff_toolset_router_enabled({}, "discord") is True


def test_router_enabled_by_env_yes(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "yes")
    assert biff_toolset_router_enabled({}, "discord") is True


def test_router_env_wins_over_config_false(monkeypatch):
    """Env var '1' beats explicit platform config false."""
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "1")
    config = {"biff": {"platforms": {"discord": {"toolset_router": False}}}}
    assert biff_toolset_router_enabled(config, "discord") is True


def test_router_env_wins_over_config_true(monkeypatch):
    """Env var '0' beats platform config true."""
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "0")
    config = {"biff": {"platforms": {"discord": {"toolset_router": True}}}}
    assert biff_toolset_router_enabled(config, "discord") is False


def test_router_enabled_by_config_bool_true(monkeypatch):
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    config = {"biff": {"platforms": {"discord": {"toolset_router": True}}}}
    assert biff_toolset_router_enabled(config, "discord") is True


def test_router_enabled_by_config_string_true(monkeypatch):
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    config = {"biff": {"platforms": {"discord": {"toolset_router": "true"}}}}
    assert biff_toolset_router_enabled(config, "discord") is True


def test_router_disabled_by_config_bool_false(monkeypatch):
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    config = {"biff": {"platforms": {"discord": {"toolset_router": False}}}}
    assert biff_toolset_router_enabled(config, "discord") is False


def test_router_disabled_by_config_string_false(monkeypatch):
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    config = {"biff": {"platforms": {"discord": {"toolset_router": "0"}}}}
    assert biff_toolset_router_enabled(config, "discord") is False


def test_router_enabled_by_use_toolset_router_alias(monkeypatch):
    """Legacy 'use_toolset_router' key works as fallback."""
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    config = {"biff": {"platforms": {"discord": {"use_toolset_router": True}}}}
    assert biff_toolset_router_enabled(config, "discord") is True


def test_router_toolset_router_wins_over_use_alias(monkeypatch):
    """toolset_router key takes priority over use_toolset_router."""
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    config = {
        "biff": {
            "platforms": {
                "discord": {"toolset_router": True, "use_toolset_router": False}
            }
        }
    }
    assert biff_toolset_router_enabled(config, "discord") is True


def test_router_absent_platform_config_uses_default_false(monkeypatch):
    """Platform config exists but no toolset_router key → false."""
    monkeypatch.delenv("HERMES_BIFF_TOOLSET_ROUTER", raising=False)
    config = {"biff": {"platforms": {"discord": {}}}}
    assert biff_toolset_router_enabled(config, "discord") is False


# ── route_class_for_plan ────────────────────────────────────────────


def test_route_conservative_for_route_bundle_action():
    assert route_class_for_plan(SimpleNamespace(action="route_bundle", runtime="", toolset_profile="")) == "conservative_full"


def test_route_conservative_for_command_action():
    assert route_class_for_plan(SimpleNamespace(action="command", runtime="", toolset_profile="")) == "conservative_full"


def test_route_conservative_for_workflow_runtime():
    assert route_class_for_plan(SimpleNamespace(action="", runtime="workflow", toolset_profile="")) == "conservative_full"


def test_route_conservative_for_continuation_runtime():
    assert route_class_for_plan(SimpleNamespace(action="", runtime="continuation", toolset_profile="")) == "conservative_full"


def test_route_conservative_for_tool_access_recovery():
    assert route_class_for_plan(SimpleNamespace(action="", runtime="tool_access_recovery", toolset_profile="")) == "conservative_full"


def test_route_conservative_for_specialist_work_runtime():
    assert route_class_for_plan(SimpleNamespace(action="", runtime="specialist_work", toolset_profile="")) == "conservative_full"


def test_route_conservative_for_unrecognized_action():
    """Unknown action + no runtime/profile → conservative_full."""
    assert route_class_for_plan(SimpleNamespace(action="unknown", runtime="", toolset_profile="")) == "conservative_full"


def test_route_conservative_for_empty_plan():
    assert route_class_for_plan(SimpleNamespace(action="", runtime="", toolset_profile="")) == "conservative_full"


def test_route_uses_nonempty_profile():
    """Non-empty toolset_profile takes priority."""
    assert route_class_for_plan(SimpleNamespace(action="", runtime="", toolset_profile="terminal")) == "terminal"


def test_route_conservative_action_overrides_profile():
    """Conservative action check runs before profile — route_bundle stays conservative_full."""
    assert route_class_for_plan(SimpleNamespace(action="route_bundle", runtime="", toolset_profile="memory")) == "conservative_full"


def test_route_conservative_runtime_overrides_profile():
    """Conservative runtime check runs before profile — workflow stays conservative_full."""
    assert route_class_for_plan(SimpleNamespace(action="", runtime="workflow", toolset_profile="no_tool")) == "conservative_full"


# ── select_biff_toolsets_with_router ───────────────────────────────


def test_select_conservative_full_preserves_incoming_surface():
    decision = select_biff_toolsets_with_router(
        plan=SimpleNamespace(action="command", runtime="", toolset_profile=""),
        enabled_toolsets=["terminal", "file"],
        configured_toolsets=["terminal", "file", "web"],
        profile_toolsets={},
    )
    assert decision.fallback is True
    assert decision.route_class == "conservative_full"
    assert list(decision.selected_toolsets) == ["file", "terminal"]


def test_select_known_route_intersects():
    decision = select_biff_toolsets_with_router(
        plan=SimpleNamespace(action="", runtime="", toolset_profile="terminal"),
        enabled_toolsets=["terminal", "file", "memory", "web"],
        configured_toolsets=["terminal", "file", "memory", "web", "kanban"],
        profile_toolsets={
            "terminal": ["terminal", "file"],
        },
    )
    assert decision.fallback is False
    assert decision.route_class == "terminal"
    assert list(decision.selected_toolsets) == ["file", "terminal"]


def test_select_route_adds_configured_missing_toolsets():
    """Toolsets in the profile that are also in the configured ceiling get added."""
    decision = select_biff_toolsets_with_router(
        plan=SimpleNamespace(action="", runtime="", toolset_profile="memory"),
        enabled_toolsets=["terminal"],
        configured_toolsets=["terminal", "memory", "session_search"],
        profile_toolsets={
            "memory": ["memory", "session_search"],
        },
    )
    assert decision.fallback is False
    # "terminal" from enabled (intersection: not in profile, not in configured→profile
    # Wait: terminal IS in enabled. But memory profile says only memory, session_search.
    # terminal is in enabled but not in profile nor in configured_profile intersection.
    # Let me recheck logic:
    # allowed_set = {"memory", "session_search"}
    # selected = {toolset for toolset in original if toolset in allowed_set} = {} (terminal not in profile)
    # for toolset in allowed_set: if toolset in configured: selected.add(toolset)
    # configured has "terminal", "memory", "session_search"
    # memory and session_search are both in configured, so they get added.
    # Result = {"memory", "session_search"}
    assert "memory" in decision.selected_toolsets
    assert "session_search" in decision.selected_toolsets


def test_select_removes_toolsets_outside_route():
    decision = select_biff_toolsets_with_router(
        plan=SimpleNamespace(action="", runtime="", toolset_profile="no_tool"),
        enabled_toolsets=["terminal", "file", "memory", "web"],
        configured_toolsets=["terminal", "file", "memory", "web"],
        profile_toolsets={"no_tool": []},
    )
    assert decision.fallback is False
    assert list(decision.selected_toolsets) == []


def test_select_unknown_route_falls_back():
    decision = select_biff_toolsets_with_router(
        plan=SimpleNamespace(action="mystery", runtime="", toolset_profile="nonexistent"),
        enabled_toolsets=["terminal"],
        configured_toolsets=["terminal", "web"],
        profile_toolsets={"terminal": ["terminal"]},
    )
    assert decision.fallback is True
    assert decision.route_class == "conservative_full"
    assert list(decision.selected_toolsets) == ["terminal"]


def test_select_empty_enabled_is_empty():
    decision = select_biff_toolsets_with_router(
        plan=SimpleNamespace(action="", runtime="", toolset_profile="terminal"),
        enabled_toolsets=[],
        configured_toolsets=["terminal"],
        profile_toolsets={"terminal": ["terminal"]},
    )
    assert decision.fallback is False
    assert list(decision.selected_toolsets) == ["terminal"]


def test_select_empty_configured_falls_back_to_enabled():
    """When configured_toolsets is empty, enabled_toolsets acts as ceiling."""
    decision = select_biff_toolsets_with_router(
        plan=SimpleNamespace(action="", runtime="", toolset_profile="terminal"),
        enabled_toolsets=["terminal", "web"],
        configured_toolsets=[],
        profile_toolsets={"terminal": ["terminal"]},
    )
    assert decision.fallback is False
    assert list(decision.selected_toolsets) == ["terminal"]


def test_select_ceiling_respected_no_extra_profile_tools():
    """Toolsets in profile but NOT in configured ceiling are excluded."""
    decision = select_biff_toolsets_with_router(
        plan=SimpleNamespace(action="", runtime="", toolset_profile="full"),
        enabled_toolsets=["terminal"],
        configured_toolsets=["terminal"],
        profile_toolsets={"full": ["terminal", "memory", "web"]},
    )
    assert decision.fallback is False
    # Only terminal is in configured, so memory and web are excluded
    assert list(decision.selected_toolsets) == ["terminal"]


def test_select_sorted_output():
    decision = select_biff_toolsets_with_router(
        plan=SimpleNamespace(action="", runtime="", toolset_profile="terminal"),
        enabled_toolsets=["web", "memory", "terminal", "file"],
        configured_toolsets=["terminal", "file", "web", "memory"],
        profile_toolsets={"terminal": ["file", "terminal"]},
    )
    assert list(decision.selected_toolsets) == ["file", "terminal"]


def test_select_with_profile_as_conservative_route():
    """When route_class_for_plan returns a profile that matches a profile entry, route applies it."""
    decision = select_biff_toolsets_with_router(
        plan=SimpleNamespace(action="", runtime="", toolset_profile="status"),
        enabled_toolsets=["terminal", "file", "kanban", "web"],
        configured_toolsets=["terminal", "file", "kanban", "web", "memory"],
        profile_toolsets={"status": ["terminal"]},
    )
    assert decision.fallback is False
    assert decision.route_class == "status"
    assert list(decision.selected_toolsets) == ["terminal"]


# ── BiffToolsetRouteDecision ────────────────────────────────────────


def test_route_decision_to_dict():
    decision = BiffToolsetRouteDecision(
        route_class="terminal",
        selected_toolsets=("file", "terminal"),
        fallback=False,
        reason="selected toolsets for route class terminal",
    )
    assert decision.to_dict() == {
        "route_class": "terminal",
        "selected_toolsets": ["file", "terminal"],
        "fallback": False,
        "reason": "selected toolsets for route class terminal",
    }


def test_route_decision_frozen():
    """BiffToolsetRouteDecision is a frozen dataclass — no mutation."""
    decision = BiffToolsetRouteDecision(
        route_class="none",
        selected_toolsets=(),
        fallback=False,
        reason="test",
    )
    with pytest.raises(AttributeError):
        decision.route_class = "something"  # type: ignore[misc]