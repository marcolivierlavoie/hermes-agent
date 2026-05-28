from __future__ import annotations

import json

from agent.biff_intent_router import plan_biff_turn
from gateway import biff_fast_memory, biff_hot_context
from gateway.biff_memory_tiers import classify_biff_memory_tier
from gateway.session_hygiene import apply_biff_turn_toolset_plan


class FakeMnemosyne:
    def handle_tool_call(self, tool_name, args):
        assert tool_name == "mnemosyne_memory"
        if args["action"] == "memory_digest":
            return json.dumps(
                {
                    "success": True,
                    "needs_decision": {"pending_candidate_count": 2, "conflict_memory_ids": []},
                    "fyi": {"trusted_memory_count": 9, "active_suppression_count": 1},
                }
            )
        if args["action"] == "recall_policy":
            return json.dumps(
                {
                    "success": True,
                    "authority_order": ["current_user_instruction", "mnemosyne_trusted_memory"],
                }
            )
        raise AssertionError(args)

    def recall(self, query, *, limit, include_suppressed):
        return [
            {
                "memory": {
                    "id": "mem-safe",
                    "content": "Biff uses Kanban as source of truth and preserves role-consent policy.",
                    "sensitivity": "non_sensitive",
                    "current_request_safe": True,
                }
            }
        ]


def _tier(message: str):
    return classify_biff_memory_tier(message, plan_biff_turn(message, command=False))


def test_memory_tier_routes_casual_turns_to_no_memory():
    decision = _tier("hi")

    assert decision.tier == "no-memory"
    assert decision.allow_mnemosyne_snapshot is False
    assert decision.require_memory_tool_lane is False


def test_memory_tier_routes_preference_lookup_to_normal_memory():
    decision = _tier("What do you remember about my Biff tool preferences?")

    assert decision.tier == "normal-memory"
    assert decision.allow_mnemosyne_snapshot is True
    assert decision.require_memory_tool_lane is True


def test_memory_tier_routes_remember_this_to_normal_memory_candidate_flow():
    decision = _tier("Remember this: use Tailscale URLs for homelab bookmarks.")

    assert decision.tier == "normal-memory"
    assert "candidate" in decision.reason or "writeback" in decision.reason
    assert decision.require_memory_tool_lane is True


def test_memory_tier_routes_history_and_resume_to_full_memory_history():
    for message in (
        "What did we discuss in the previous session about tool routing?",
        "Where did we leave off after the chat refreshed?",
    ):
        decision = _tier(message)
        assert decision.tier == "full-memory-history"
        assert decision.max_snapshot_chars >= 1800
        assert decision.require_memory_tool_lane is True


def test_memory_tier_keeps_biff_os_and_safety_policy_on_compact_memory():
    for message in (
        "Continue BIF-1525 and keep role-consent policy intact.",
        "Check the Kanban story status for BIF-1525.",
    ):
        decision = _tier(message)
        assert decision.tier == "compact-memory"
        assert decision.allow_mnemosyne_snapshot is True
        assert decision.require_memory_tool_lane is False


def test_memory_toolset_plan_preserves_mnemosyne_tools_for_remember_this(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "1")
    configured = ["terminal", "file", "memory", "session_search", "todo", "kanban", "web"]

    enabled = apply_biff_turn_toolset_plan(
        {},
        "discord",
        configured,
        configured_toolsets=configured,
        message="Remember this: use Tailscale URLs for homelab bookmarks.",
    )

    assert enabled == ["memory", "session_search", "terminal"]


def test_hot_context_omits_mnemosyne_snapshot_for_no_memory_casual(monkeypatch):
    monkeypatch.setattr(biff_fast_memory, "_provider", lambda: FakeMnemosyne())
    biff_hot_context.clear_biff_hot_context_cache()

    context = biff_hot_context.build_biff_hot_context({}, platform_key="discord", query="hi")

    assert "Biff Compact Identity" in context
    assert "Biff Fast Memory Snapshot" not in context
    assert "Memory tier:" not in context


def test_hot_context_includes_compact_memory_snapshot_for_biff_os(monkeypatch):
    monkeypatch.setattr(biff_fast_memory, "_provider", lambda: FakeMnemosyne())
    biff_hot_context.clear_biff_hot_context_cache()

    context = biff_hot_context.build_biff_hot_context(
        {},
        platform_key="discord",
        query="Continue BIF-1525 and keep role-consent policy intact.",
    )

    assert "Memory tier: compact-memory" in context
    assert "Biff Fast Memory Snapshot" in context
    assert "role-consent policy" in context


def test_hot_context_expands_snapshot_budget_for_history(monkeypatch):
    monkeypatch.setattr(biff_fast_memory, "_provider", lambda: FakeMnemosyne())
    biff_hot_context.clear_biff_hot_context_cache()

    context = biff_hot_context.build_biff_hot_context(
        {},
        platform_key="discord",
        query="What did we discuss in the previous session about tool routing?",
    )

    assert "Memory tier: full-memory-history" in context
    assert "Biff Fast Memory Snapshot" in context
    assert "history/session recovery" in context
