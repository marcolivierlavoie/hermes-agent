from __future__ import annotations

import ast
import inspect
import textwrap

from gateway.run import GatewayRunner
from gateway.session_hygiene import apply_biff_tool_schema_profile, apply_biff_turn_toolset_plan
from gateway.biff_router_telemetry import build_biff_router_telemetry_context


CONFIGURED = [
    "terminal",
    "file",
    "memory",
    "session_search",
    "todo",
    "kanban",
    "web",
    "search",
    "browser",
    "vision",
]


def _run_agent_source() -> str:
    return textwrap.dedent(inspect.getsource(GatewayRunner._run_agent))


def _run_sync_ast() -> ast.FunctionDef:
    tree = ast.parse(_run_agent_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_sync":
            return node
    raise AssertionError("GatewayRunner._run_agent no longer defines run_sync")


def test_router_disabled_preserves_profiled_discord_surface(monkeypatch):
    """Planner narrows 'hi' to empty even without the router.

    The router is an additional enforcement layer on top of planner narrowing,
    not a replacement.  'Router disabled' means only the planner narrows.
    """
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "0")
    profiled = apply_biff_tool_schema_profile({}, "discord", CONFIGURED)

    enabled = apply_biff_turn_toolset_plan(
        {},
        "discord",
        profiled,
        message="hi",
        configured_toolsets=CONFIGURED,
    )

    assert enabled == []
    assert "terminal" not in enabled


def test_router_enabled_routes_short_reply_to_no_tool_surface(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "1")
    profiled = apply_biff_tool_schema_profile({}, "discord", CONFIGURED)

    enabled = apply_biff_turn_toolset_plan(
        {},
        "discord",
        profiled,
        message="hi",
        configured_toolsets=CONFIGURED,
    )
    telemetry = build_biff_router_telemetry_context(
        config={},
        platform_key="discord",
        message="hi",
        configured_toolsets=CONFIGURED,
        selected_toolsets=enabled,
    )

    assert enabled == []
    assert telemetry.router_enabled is True
    assert telemetry.route == "none"
    assert telemetry.fallback_full_surface is False


def test_router_enabled_routes_tool_lookup_and_biff_os_execution_safely(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "1")
    profiled = apply_biff_tool_schema_profile({}, "discord", CONFIGURED)

    tool_lookup = apply_biff_turn_toolset_plan(
        {},
        "discord",
        profiled,
        message="What time is it?",
        configured_toolsets=CONFIGURED,
    )
    biff_os_execution = apply_biff_turn_toolset_plan(
        {},
        "discord",
        profiled,
        message="Continue BIF-1527 and run the focused gateway tests.",
        configured_toolsets=CONFIGURED,
    )
    execution_telemetry = build_biff_router_telemetry_context(
        config={},
        platform_key="discord",
        message="Continue BIF-1527 and run the focused gateway tests.",
        configured_toolsets=CONFIGURED,
        selected_toolsets=biff_os_execution,
    )

    assert tool_lookup == ["file", "terminal"]
    assert set(biff_os_execution) == set(profiled)
    assert execution_telemetry.route == "conservative_full"
    assert execution_telemetry.fallback_full_surface is True


def test_router_enabled_preserves_memory_lane_for_fallback_provider_quality(monkeypatch):
    """Provider fallback labels are telemetry-only; router must not strip memory work."""

    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "1")
    profiled = apply_biff_tool_schema_profile({}, "discord", CONFIGURED)

    enabled = apply_biff_turn_toolset_plan(
        {},
        "discord",
        profiled,
        message="Remember this: fallback replies should be labeled as degraded.",
        configured_toolsets=CONFIGURED,
    )
    telemetry = build_biff_router_telemetry_context(
        config={},
        platform_key="discord",
        message="Remember this: fallback replies should be labeled as degraded.",
        configured_toolsets=CONFIGURED,
        selected_toolsets=enabled,
    )

    assert enabled == ["memory", "session_search", "terminal"]
    assert telemetry.memory_tier == "normal-memory"
    assert telemetry.router_enabled is True


def test_gateway_cache_signature_includes_per_turn_toolsets():
    wide = GatewayRunner._agent_config_signature(
        "model",
        {"provider": "openai", "api_key": "secret-a", "base_url": "", "api_mode": "chat"},
        ["terminal", "file", "memory", "kanban"],
        "prompt",
        cache_keys={"context_length": 1000},
    )
    narrow = GatewayRunner._agent_config_signature(
        "model",
        {"provider": "openai", "api_key": "secret-a", "base_url": "", "api_mode": "chat"},
        [],
        "prompt",
        cache_keys={"context_length": 1000},
    )

    assert wide != narrow


def test_run_sync_records_router_telemetry_after_final_enabled_toolsets_and_result():
    source = _run_agent_source()
    plan_idx = source.index("_planned_toolsets = apply_biff_turn_toolset_plan(")
    enabled_idx = source.index("enabled_toolsets = filter_biff_mode_enabled_toolsets(")
    telemetry_context_idx = source.index("build_biff_router_telemetry_context(")
    run_idx = source.index("result = agent.run_conversation(")
    record_idx = source.index("record_biff_router_turn(")

    assert plan_idx < enabled_idx < telemetry_context_idx < run_idx < record_idx


def test_run_sync_sets_recall_ceiling_and_evicts_before_telemetry_closeout():
    source = _run_agent_source()
    ceiling_idx = source.index("agent._toolset_recall_ceiling = list(_configured_toolsets)")
    run_idx = source.index("result = agent.run_conversation(")
    evict_idx = source.index("self._evict_cached_agent(session_key)", run_idx)
    record_idx = source.index("record_biff_router_turn(")

    assert ceiling_idx < run_idx < evict_idx < record_idx
