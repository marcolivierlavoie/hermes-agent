"""BIF-1510: Tool availability / emergency repair — regression tests.

Tests cover:
  1. User asks why tools are gone during evidence-only mode
  2. User asks to restore tools (should trigger tool_access_recovery)
  3. User invokes runtime repair bundle (biff-hermes-runtime-change)
  4. Post-rollover continuation must regain base operator tools
  5. Synthetic schema check: required tools present in approved contexts
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.session_hygiene import (
    BiffOperatingMode,
    BiffRuntimeInstabilitySignal,
    _BIFF_MODE_SPECS,
    apply_biff_runtime_instability_guard,
    apply_biff_tool_schema_profile,
    apply_biff_turn_toolset_plan,
    filter_biff_mode_enabled_toolsets,
    relax_biff_runtime_instability_guard_for_turn,
    resolve_biff_operating_mode,
    widen_biff_toolsets_for_bundle,
)


def _extreme_signal() -> BiffRuntimeInstabilitySignal:
    """Simulate extreme runtime instability (≥8 SIGTERM)."""
    return BiffRuntimeInstabilitySignal(
        active=True,
        reasons=("recent_gateway_restarts", "codex_empty_terminal_frames"),
        sigterm_count=8,
        codex_empty_output_count=3,
        repeated_failure_count=0,
        severity="extreme",
    )


def _soft_signal() -> BiffRuntimeInstabilitySignal:
    """Simulate soft instability (repeated tool loop)."""
    return BiffRuntimeInstabilitySignal(
        active=True,
        reasons=("repeated_tool_or_long_turn_loop",),
        sigterm_count=1,
        codex_empty_output_count=0,
        repeated_failure_count=3,
        severity="soft",
    )


BIFF_V3_CEILING = [
    "terminal", "file", "memory", "skills-read", "todo",
    "kanban", "web", "search", "discord",
]

BIFF_V3_CEILING_FULL = [
    "terminal", "file", "memory", "skills-read", "todo",
    "kanban", "web", "search", "discord",
    "code_execution", "delegation", "skills", "vision",
]


# ---------------------------------------------------------------------------
# Test 1: User asks why tools are gone
# ---------------------------------------------------------------------------


class TestUserAsksWhyToolsAreGone:
    """When a user asks why tools are missing, the system should detect this
    as a tool_access_recovery route and restore emergency-mode tools."""

    def test_why_are_my_tools_gone_routes_to_recovery(self):
        """Simulate the user asking 'why are my tools gone'. The planner
        should recognize this as a tool_access_recovery turn."""
        from agent.biff_intent_router import plan_biff_turn

        plan = plan_biff_turn("Why are all my tools gone?", command=False)

        # The runtime should be tool_access_recovery or continuation
        assert plan.runtime in (
            "tool_access_recovery", "continuation", "direct_answer",
        ), f"Expected recovery-related runtime, got {plan.runtime}"


    def test_instability_guard_does_not_strip_tools_for_recovery_route(self):
        """Even during extreme instability, a tool_access_recovery turn must
        have terminal, file, kanban tools available."""
        mode = resolve_biff_operating_mode({}, "discord")
        signal = _extreme_signal()
        degraded = apply_biff_runtime_instability_guard(mode, signal)
        relaxed = relax_biff_runtime_instability_guard_for_turn(
            degraded,
            signal,
            route_runtime="tool_access_recovery",
            route_action="route_bundle",
        )

        assert degraded.name == "evidence-only"
        assert relaxed.name == "emergency"

        enabled = filter_biff_mode_enabled_toolsets(
            relaxed,
            BIFF_V3_CEILING,
        )
        assert "terminal" in enabled
        assert "file" in enabled
        assert "kanban" in enabled


# ---------------------------------------------------------------------------
# Test 2: User asks to restore tools
# ---------------------------------------------------------------------------


class TestUserAsksToRestoreTools:
    """When a user explicitly asks to restore tools, the system must recover
    the full emergency tool profile."""

    def test_restore_tools_prompt_keeps_operator_tools(self):
        """Simulate a turn where the user asks 'restore my tools'.
        The guard should relax to emergency mode."""
        mode = resolve_biff_operating_mode({}, "discord")
        signal = _extreme_signal()
        degraded = apply_biff_runtime_instability_guard(mode, signal)

        relaxed = relax_biff_runtime_instability_guard_for_turn(
            degraded,
            signal,
            route_runtime="continuation",
            route_action="resume_context",
        )

        assert degraded.name == "evidence-only"
        assert relaxed.name == "emergency"

        enabled = filter_biff_mode_enabled_toolsets(
            relaxed,
            BIFF_V3_CEILING,
        )
        assert "terminal" in enabled
        assert "file" in enabled
        assert "web" in enabled
        assert "kanban" in enabled


# ---------------------------------------------------------------------------
# Test 3: Runtime repair bundle invocation
# ---------------------------------------------------------------------------


class TestRuntimeRepairBundle:
    """When a user invokes the biff-hermes-runtime-change bundle during
    instability, the guard must relax to emergency mode and the bundle
    escalation must add code_execution and delegation tools."""

    def test_runtime_change_bundle_preserves_repair_tools(self):
        """The runtime-change bundle gets code_execution + delegation added
        via bundle escalation, even during extreme instability."""
        mode = resolve_biff_operating_mode({}, "discord")
        signal = _extreme_signal()
        degraded = apply_biff_runtime_instability_guard(mode, signal)

        relaxed = relax_biff_runtime_instability_guard_for_turn(
            degraded,
            signal,
            route_runtime="biff-hermes-runtime-change",
            route_action="route_bundle",
        )

        assert degraded.name == "evidence-only"
        assert relaxed.name == "emergency"

        message = '[IMPORTANT: The user has invoked the "biff-hermes-runtime-change" skill bundle.]'
        planned = apply_biff_turn_toolset_plan(
            {}, "discord", BIFF_V3_CEILING, message=message,
        )
        widened = widen_biff_toolsets_for_bundle(
            {}, "discord", planned, BIFF_V3_CEILING_FULL, message=message,
        )
        enabled = filter_biff_mode_enabled_toolsets(relaxed, widened)

        assert "terminal" in enabled
        assert "file" in enabled
        assert "kanban" in enabled
        assert "code_execution" in enabled
        assert "delegation" in enabled


# ---------------------------------------------------------------------------
# Test 4: Post-rollover continuation regains base operator tools
# ---------------------------------------------------------------------------


class TestPostRolloverContinuation:
    """After a session rollover (chat refresh), the continuation turn must
    regain terminal, file, kanban, and session_search tools even during
    instability."""

    def test_continuation_turn_after_rollover_has_operator_tools(self):
        """Simulate a post-rollover continuation turn."""
        mode = resolve_biff_operating_mode({}, "discord")
        signal = _extreme_signal()
        degraded = apply_biff_runtime_instability_guard(mode, signal)

        relaxed = relax_biff_runtime_instability_guard_for_turn(
            degraded,
            signal,
            route_runtime="continuation",
            route_action="route_bundle",
        )

        assert degraded.name == "evidence-only"
        assert relaxed.name == "emergency"

        enabled = filter_biff_mode_enabled_toolsets(
            relaxed,
            BIFF_V3_CEILING,
        )
        assert "terminal" in enabled
        assert "file" in enabled
        assert "kanban" in enabled
        assert "memory" in enabled

    def test_resume_context_action_restores_recovery_tools(self):
        """The resume_context action must trigger emergency mode restoration."""
        mode = resolve_biff_operating_mode({}, "discord")
        signal = _extreme_signal()
        degraded = apply_biff_runtime_instability_guard(mode, signal)

        relaxed = relax_biff_runtime_instability_guard_for_turn(
            degraded,
            signal,
            route_runtime="context_resume",
            route_action="resume_context",
        )

        assert degraded.name == "evidence-only"
        assert relaxed.name == "emergency"

        enabled = filter_biff_mode_enabled_toolsets(
            relaxed,
            BIFF_V3_CEILING,
        )
        assert "terminal" in enabled
        assert "file" in enabled
        assert "kanban" in enabled

    def test_ordinary_continuation_does_not_get_evidence_only_mode(self):
        """A plain continuation that's not during instability should stay at
        the original mode if no signal is present."""
        mode = resolve_biff_operating_mode({}, "discord")
        relaxed = relax_biff_runtime_instability_guard_for_turn(
            mode,
            None,  # no instability signal
            route_runtime="continuation",
            route_action="route_bundle",
        )

        # No signal → no change
        assert relaxed.name == mode.name


# ---------------------------------------------------------------------------
# Test 5: Synthetic schema check — required tools in approved contexts
# ---------------------------------------------------------------------------


MINIMUM_EMERGENCY_TOOLSETS = frozenset({
    "terminal", "file", "kanban", "memory", "todo",
    "web", "search",
})

MINIMUM_EMERGENCY_TOOLSETS_BUNDLE = frozenset({
    "terminal", "file", "kanban", "memory", "todo",
    "web", "search", "skills",
})


class TestSyntheticSchemaCheck:
    """Prove that the resolved tool surface includes required tools in
    every approved context, even under extreme instability."""

    @pytest.mark.parametrize("route_runtime,route_action,msg,expected_extra", [
        ("tool_access_recovery", "route_bundle", None, set()),
        ("continuation", "route_bundle", None, set()),
        ("kanban_admin", "kanban_admin", None, set()),
        ("kanban_read", "kanban_status", None, set()),
        ("biff-hermes-runtime-change", "route_bundle",
         '[IMPORTANT: The user has invoked the "biff-hermes-runtime-change" skill bundle.]',
         {"code_execution", "delegation"}),
        ("biff-issue-execution", "route_bundle",
         '[IMPORTANT: The user has invoked the "biff-issue-execution" skill bundle.]',
         {"code_execution", "delegation"}),
    ])
    def test_approved_context_has_minimum_tools(
        self, route_runtime, route_action, msg, expected_extra,
    ):
        """For each approved recovery/continuation context, verify the
        resolved tool surface includes the minimum emergency toolset, plus
        any bundle-extra tools."""
        mode = resolve_biff_operating_mode({}, "discord")
        signal = _extreme_signal()
        degraded = apply_biff_runtime_instability_guard(mode, signal)
        relaxed = relax_biff_runtime_instability_guard_for_turn(
            degraded, signal,
            route_runtime=route_runtime,
            route_action=route_action,
        )

        assert relaxed.name == "emergency", (
            f"{route_runtime}/{route_action} should relax to emergency, "
            f"got {relaxed.name}"
        )

        ceiling = BIFF_V3_CEILING if not expected_extra else BIFF_V3_CEILING_FULL
        if msg:
            planned = apply_biff_turn_toolset_plan(
                {}, "discord", ceiling, message=msg,
            )
            widened = widen_biff_toolsets_for_bundle(
                {}, "discord", planned, ceiling, message=msg,
            )
        else:
            planned = apply_biff_turn_toolset_plan(
                {}, "discord", ceiling,
            )
            widened = widen_biff_toolsets_for_bundle(
                {}, "discord", planned, ceiling,
            )

        enabled = filter_biff_mode_enabled_toolsets(relaxed, widened)

        required_set = MINIMUM_EMERGENCY_TOOLSETS_BUNDLE if expected_extra else MINIMUM_EMERGENCY_TOOLSETS
        missing = required_set - set(enabled)
        assert not missing, (
            f"Context {route_runtime}/{route_action} is missing "
            f"required toolsets: {missing}"
        )
        for extra in expected_extra:
            assert extra in enabled, (
                f"Context {route_runtime}/{route_action} missing "
                f"bundle-extra toolset {extra}"
            )

    def test_unapproved_context_gets_no_terminal_in_evidence_only(self):
        """An ordinary chat turn during extreme instability must NOT have
        terminal, file, or kanban tools (evidence-only filter kicks in)."""
        mode = resolve_biff_operating_mode({}, "discord")
        signal = _extreme_signal()
        degraded = apply_biff_runtime_instability_guard(mode, signal)

        # No relaxation: stays evidence-only
        enabled = filter_biff_mode_enabled_toolsets(
            degraded,
            BIFF_V3_CEILING,
        )

        assert degraded.name == "evidence-only"
        assert "terminal" not in enabled
        assert "file" not in enabled
        assert "kanban" not in enabled
        # Evidence-only safe toolsets should remain
        assert "search" in enabled
        assert "web" in enabled