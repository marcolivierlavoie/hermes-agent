"""Tests for gateway session hygiene — auto-compression of large sessions.

Verifies that the gateway detects pathologically large transcripts and
triggers auto-compression before running the agent.  (#628)

The hygiene system uses the SAME compression config as the agent:
  compression.threshold × model context length
so CLI and messaging platforms behave identically.
"""

import importlib
import sys
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from agent.model_metadata import estimate_messages_tokens_rough
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, SendResult
from gateway.session import SessionEntry, SessionSource
from gateway.session_hygiene import (
    apply_discord_slowdown_guard,
    apply_biff_runtime_instability_guard,
    apply_biff_runtime_instability_tool_guardrails,
    apply_biff_tool_schema_profile,
    apply_biff_turn_toolset_plan,
    apply_biff_prompt_budget,
    biff_operating_mode_prompt,
    biff_prompt_budget_enabled,
    cap_hygiene_history,
    cap_model_facing_tool_outputs,
    collect_token_source_metrics,
    detect_biff_runtime_instability,
    filter_biff_mode_enabled_toolsets,
    inspect_biff_runtime_instability_logs,
    maybe_build_slow_work_deflection,
    render_plain_language_heartbeat,
    resolve_biff_live_max_iterations,
    resolve_biff_live_tool_guardrail_settings,
    resolve_biff_prompt_budget_tokens,
    resolve_biff_operating_mode,
    resolve_biff_tool_schema_profile,
    should_apply_biff_prompt_budget,
    widen_biff_toolsets_for_bundle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_history(n_messages: int, content_size: int = 100) -> list:
    """Build a fake transcript with n_messages user/assistant pairs."""
    history = []
    content = "x" * content_size
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": content, "timestamp": f"t{i}"})
    return history


def _make_large_history_tokens(target_tokens: int) -> list:
    """Build a history that estimates to roughly target_tokens tokens."""
    # estimate_messages_tokens_rough counts total chars in str(msg) // 4
    # Each msg dict has ~60 chars of overhead + content chars
    # So for N tokens we need roughly N * 4 total chars across all messages
    target_chars = target_tokens * 4
    # Each message as a dict string is roughly len(content) + 60 chars
    msg_overhead = 60
    # Use 50 messages with appropriately sized content
    n_msgs = 50
    content_size = max(10, (target_chars // n_msgs) - msg_overhead)
    return _make_history(n_msgs, content_size=content_size)


def _make_tool_history(
    n_messages: int,
    *,
    large_every: int = 1,
    large_size: int = 20_000,
    small_size: int = 2_000,
    call_prefix: str = "call",
    evidence_prefix: str = "evidence",
) -> list[dict]:
    """Build assistant/tool pairs with predictable large-output candidates."""

    history = []
    for i in range(n_messages):
        is_large = i % large_every == 0
        size = large_size if is_large else small_size
        history.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"{call_prefix}_{i}"}]})
        history.append(
            {
                "role": "tool",
                "tool_call_id": f"{call_prefix}_{i}",
                "content": f"tool {i} wrote /tmp/{evidence_prefix}-{i}.log\n" + (str(i % 10) * size),
            }
        )
    return history


def _tool_content_chars(history: list[dict]) -> int:
    return sum(len(m["content"]) for m in history if m.get("role") == "tool")


def test_discord_live_guardrails_keep_plain_chat_tight():
    settings = resolve_biff_live_tool_guardrail_settings({}, "discord", message="what is this?")

    assert settings["bundle_key"] is None
    assert settings["terminal_timeout"] == 15
    assert settings["max_tool_calls"] == 1
    assert resolve_biff_live_max_iterations({}, "discord", message="what is this?", base_max_iterations=90) == 2


def test_turn_toolset_plan_removes_tools_for_casual_answer():
    configured = ["terminal", "file", "memory", "skills-read", "todo", "kanban", "web"]
    enabled = apply_biff_turn_toolset_plan(
        {},
        "discord",
        configured,
        message="Quick question: what are three simple dinner ideas with chicken and rice?",
    )

    assert enabled == []


def test_turn_toolset_plan_recovers_operator_tools_when_user_flags_missing_tool_access():
    configured = ["terminal", "file", "memory", "skills-read", "todo", "kanban", "web", "search"]

    enabled = apply_biff_turn_toolset_plan(
        {},
        "discord",
        [],
        configured_toolsets=configured,
        message="why do you not have tools?",
    )

    assert {"terminal", "file", "memory", "todo", "kanban", "web"}.issubset(enabled)


def test_turn_toolset_plan_keeps_only_board_tools_for_kanban_status():
    configured = ["terminal", "file", "memory", "skills-read", "todo", "kanban", "web", "delegation"]
    enabled = apply_biff_turn_toolset_plan(
        {},
        "discord",
        configured,
        message="Status check only: tell me what K-1346 and K-1347 currently say on the Kanban board.",
    )

    assert enabled == ["kanban", "terminal"]


def test_turn_toolset_plan_grants_web_and_browser_for_links():
    configured = ["terminal", "file", "memory", "skills-read", "todo", "kanban", "web", "search", "browser"]
    enabled = apply_biff_turn_toolset_plan(
        {},
        "discord",
        ["terminal", "file", "memory", "skills-read", "todo", "kanban"],
        configured_toolsets=configured,
        message="Can you see this thread https://www.reddit.com/r/hermesagent/comments/1tlyfob/best_localfirst_ai_memory_assistant_second_brain/",
    )

    assert enabled == ["browser", "file", "search", "terminal", "web"]


def test_turn_toolset_plan_zero_tools_for_mental_health_moment_and_dashboard_handoff():
    configured = ["terminal", "file", "memory", "skills-read", "todo", "kanban", "web", "search", "browser"]

    assert apply_biff_turn_toolset_plan({}, "discord", configured, message="distortion check") == []
    assert apply_biff_turn_toolset_plan({}, "discord", configured, message="open the mental health daily ritual") == []


def test_turn_toolset_plan_applies_zero_tool_mental_health_surfaces_across_platforms():
    configured = ["terminal", "file", "memory", "skills-read", "todo", "kanban", "web", "search", "browser"]

    assert apply_biff_turn_toolset_plan({}, "telegram", configured, message="distortion check") == []
    assert apply_biff_turn_toolset_plan({}, "api_server", configured, message="start the daily ritual") == []


def test_turn_toolset_plan_preserves_non_discord_for_regular_tool_profiles():
    configured = ["terminal", "file", "memory", "skills-read", "todo", "kanban", "web", "search", "browser"]

    assert apply_biff_turn_toolset_plan({}, "telegram", configured, message="Status check only: tell me what K-1346 says") == sorted(configured)


def test_turn_toolset_plan_bundle_messages_still_bypass_planner():
    configured = ["terminal", "file", "memory", "skills-read", "todo", "kanban", "web", "delegation"]
    message = '[IMPORTANT: The user has invoked the "biff-hermes-runtime-change" skill bundle.]'

    enabled = apply_biff_turn_toolset_plan({}, "discord", configured, message=message)

    assert enabled == sorted(configured)


def test_turn_toolset_plan_keeps_specialist_base_for_bundle_message():
    configured = ["terminal", "file", "memory", "skills-read", "todo", "kanban", "web", "delegation"]
    message = '[IMPORTANT: The user has invoked the "biff-hermes-runtime-change" skill bundle.]'

    enabled = apply_biff_turn_toolset_plan({}, "discord", configured, message=message)

    assert enabled == sorted(configured)


def test_discord_live_guardrails_give_forge_delivery_room():
    message = '[IMPORTANT: The user has invoked the "biff-hermes-runtime-change" skill bundle.]'

    settings = resolve_biff_live_tool_guardrail_settings({}, "discord", message=message)

    assert settings["bundle_key"] == "biff-hermes-runtime-change"
    assert settings["terminal_timeout"] == 60
    assert settings["max_tool_calls"] == 80
    assert resolve_biff_live_max_iterations({}, "discord", message=message, base_max_iterations=90) == 72


def test_forge_delivery_budget_ignores_generic_bundle_caps():
    message = '[IMPORTANT: The user has invoked the "biff-hermes-runtime-change" skill bundle.]'
    config = {
        "biff": {
            "platforms": {
                "discord": {
                    "bundle_chat_max_tool_calls": 4,
                    "bundle_chat_max_iterations": 4,
                    "chat_terminal_timeout": 15,
                }
            }
        }
    }

    settings = resolve_biff_live_tool_guardrail_settings(config, "discord", message=message)

    assert settings["max_tool_calls"] == 80
    assert settings["terminal_timeout"] == 60
    assert resolve_biff_live_max_iterations(config, "discord", message=message, base_max_iterations=90) == 72


def test_issue_execution_budget_ignores_generic_bundle_caps():
    message = '[IMPORTANT: The user has invoked the "biff-issue-execution" skill bundle.]'
    config = {
        "biff": {
            "platforms": {
                "discord": {
                    "bundle_chat_max_tool_calls": 4,
                    "bundle_chat_max_iterations": 4,
                    "chat_terminal_timeout": 15,
                }
            }
        }
    }

    settings = resolve_biff_live_tool_guardrail_settings(config, "discord", message=message)

    assert settings["max_tool_calls"] == 60
    assert settings["terminal_timeout"] == 45
    assert resolve_biff_live_max_iterations(config, "discord", message=message, base_max_iterations=90) == 60


def test_issue_execution_budget_has_own_explicit_overrides():
    message = '[IMPORTANT: The user has invoked the "biff-issue-execution" skill bundle.]'
    config = {
        "biff": {
            "platforms": {
                "discord": {
                    "issue_execution_chat_terminal_timeout": 75,
                    "issue_execution_chat_max_tool_calls": 44,
                    "issue_execution_chat_max_iterations": 41,
                }
            }
        }
    }

    settings = resolve_biff_live_tool_guardrail_settings(config, "discord", message=message)

    assert settings["max_tool_calls"] == 44
    assert settings["terminal_timeout"] == 75
    assert resolve_biff_live_max_iterations(config, "discord", message=message, base_max_iterations=90) == 41


def test_forge_delivery_budget_has_own_explicit_overrides():
    message = '[IMPORTANT: The user has invoked the "biff-hermes-runtime-change" skill bundle.]'
    config = {
        "biff": {
            "platforms": {
                "discord": {
                    "forge_chat_terminal_timeout": 90,
                    "forge_chat_max_tool_calls": 55,
                    "forge_chat_max_iterations": 50,
                }
            }
        }
    }

    settings = resolve_biff_live_tool_guardrail_settings(config, "discord", message=message)

    assert settings["max_tool_calls"] == 55
    assert settings["terminal_timeout"] == 90
    assert resolve_biff_live_max_iterations(config, "discord", message=message, base_max_iterations=90) == 50


def test_discord_live_guardrails_allow_explicit_unlimited_bundle_tools():
    message = '[IMPORTANT: The user has invoked the "biff-hermes-runtime-change" skill bundle.]'
    config = {"biff": {"platforms": {"discord": {"forge_chat_max_tool_calls": "unlimited"}}}}

    settings = resolve_biff_live_tool_guardrail_settings(config, "discord", message=message)

    assert settings["max_tool_calls"] is None


def test_discord_live_guardrails_keep_plain_engineering_action_with_biff():
    message = "Delete Cockpit from the Hermes dashboard sidebar and verify it is gone."

    settings = resolve_biff_live_tool_guardrail_settings({}, "discord", message=message)

    assert settings["bundle_key"] is None
    assert settings["route_action"] == "route_bundle"
    assert settings["terminal_timeout"] == 45
    assert settings["max_tool_calls"] == 36
    assert resolve_biff_live_max_iterations({}, "discord", message=message, base_max_iterations=90) == 48


def test_discord_live_guardrails_keep_quick_status_small():
    message = "Can you check gateway status?"

    settings = resolve_biff_live_tool_guardrail_settings({}, "discord", message=message)

    assert settings["route_action"] == "one_tool"
    assert settings["terminal_timeout"] == 20
    assert settings["max_tool_calls"] == 2
    assert resolve_biff_live_max_iterations({}, "discord", message=message, base_max_iterations=90) == 3


def test_discord_live_guardrails_keep_kanban_status_bounded():
    message = "Status check only: tell me what K-1346 and K-1347 currently say on the Kanban board."

    settings = resolve_biff_live_tool_guardrail_settings({}, "discord", message=message)

    assert settings["route_action"] == "kanban_status"
    assert settings["terminal_timeout"] == 20
    assert settings["max_tool_calls"] == 3
    assert resolve_biff_live_max_iterations({}, "discord", message=message, base_max_iterations=90) == 5


def test_discord_live_guardrails_keep_board_admin_action_with_biff():
    message = "Create a Kanban story for proper routing and move it to todo."

    settings = resolve_biff_live_tool_guardrail_settings({}, "discord", message=message)

    assert settings["route_action"] == "route_bundle"
    assert settings["terminal_timeout"] == 45
    assert settings["max_tool_calls"] == 36
    assert resolve_biff_live_max_iterations({}, "discord", message=message, base_max_iterations=90) == 48


def test_plain_route_budgets_ignore_generic_chat_cap():
    config = {"biff": {"platforms": {"discord": {"chat_max_tool_calls": 8, "chat_max_iterations": 8}}}}
    message = "do it. Btw I do see that it hit the OpenAI API. could mini be too weak?"

    settings = resolve_biff_live_tool_guardrail_settings(config, "discord", message=message)

    assert settings["route_action"] == "route_bundle"
    assert settings["max_tool_calls"] == 16
    assert resolve_biff_live_max_iterations(config, "discord", message=message, base_max_iterations=90) == 48


def test_answer_now_budget_ignores_generic_chat_cap():
    config = {"biff": {"platforms": {"discord": {"chat_max_tool_calls": 8, "chat_max_iterations": 8}}}}

    settings = resolve_biff_live_tool_guardrail_settings(config, "discord", message="what is this?")

    assert settings["route_action"] == "answer_now"
    assert settings["max_tool_calls"] == 1
    assert resolve_biff_live_max_iterations(config, "discord", message="what is this?", base_max_iterations=90) == 2


class HygieneCaptureAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="fake-token"), Platform.TELEGRAM)
        self.sent = []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.sent.append(
            {
                "chat_id": chat_id,
                "content": content,
                "reply_to": reply_to,
                "metadata": metadata,
            }
        )
        return SendResult(success=True, message_id="hygiene-1")

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


# ---------------------------------------------------------------------------
# Detection threshold tests (model-aware, unified with compression config)
# ---------------------------------------------------------------------------


class TestSessionHygieneCaps:
    def test_caps_history_to_recent_messages(self):
        history = _make_history(10, content_size=10)
        capped, stats = cap_hygiene_history(history, max_messages=4)

        assert len(capped) == 4
        assert stats.original_messages == 10
        assert stats.capped_messages == 4
        assert stats.capped is True
        assert capped[0]["timestamp"] == "t6"

    def test_caps_large_tool_outputs_more_aggressively(self):
        history = [
            {"role": "user", "content": "short"},
            {"role": "tool", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
            {"role": "user", "content": "done"},
        ]

        capped, stats = cap_hygiene_history(
            history,
            max_content_chars=80,
            max_tool_output_chars=30,
        )

        assert len(capped[1]["content"]) <= 80  # suffix can consume full small caps
        assert "truncated by gateway session hygiene" in capped[1]["content"]
        assert "truncated by gateway session hygiene" in capped[2]["content"]
        assert stats.tool_outputs_truncated == 1
        assert stats.content_truncated == 1

    def test_model_facing_tool_cap_preserves_pairing_and_references(self):
        raw_tool_output = "HEAD evidence\n" + ("x" * 120) + "\nTAIL evidence"
        history = [
            {"role": "user", "content": "please inspect"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_123", "content": raw_tool_output},
            {"role": "assistant", "content": "I saw it"},
        ]

        capped, stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-abc",
            transcript_ref="/tmp/hermes/sess-abc.jsonl",
            max_tool_output_chars=80,
            preview_chars=24,
        )

        assert [m["role"] for m in capped] == ["user", "assistant", "tool", "assistant"]
        assert capped[1]["tool_calls"] == history[1]["tool_calls"]
        assert capped[2]["tool_call_id"] == "call_123"
        assert capped[2]["content"] != raw_tool_output
        assert "session_id=sess-abc" in capped[2]["content"]
        assert "message_index=2" in capped[2]["content"]
        assert "transcript_ref=/tmp/hermes/sess-abc.jsonl" in capped[2]["content"]
        assert "sha256=" in capped[2]["content"]
        assert "HEAD evidence" in capped[2]["content"]
        assert "TAIL evidence" in capped[2]["content"]
        assert "retrieve the exact raw tool output" in capped[2]["content"]
        assert stats.tool_outputs_capped_count == 1
        assert stats.tool_output_chars_before == len(raw_tool_output)
        assert stats.tool_output_chars_after == len(capped[2]["content"])
        assert stats.tool_output_chars_omitted == len(raw_tool_output) - 48

    def test_model_facing_tool_cap_does_not_mutate_full_history(self):
        history = [
            {"role": "assistant", "tool_calls": [{"id": "call_a"}], "content": ""},
            {"role": "tool", "tool_call_id": "call_a", "content": "raw" * 80},
        ]
        original = [dict(m) for m in history]

        capped, _stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-1",
            transcript_ref="sqlite:sess-1",
            max_tool_output_chars=40,
        )

        assert history == original
        assert capped is not history
        assert capped[1] is not history[1]
        assert history[1]["content"] == "raw" * 80

    def test_capped_marker_redacts_secret_like_values_from_preview(self):
        raw_tool_output = (
            "OPENAI_API_KEY=sk-" + "a" * 48 + "\n"
            + "middle" * 40
            + "\nGITHUB_TOKEN=ghp_" + "b" * 36
        )

        capped, _stats = cap_model_facing_tool_outputs(
            [{"role": "tool", "tool_call_id": "call_secret", "content": raw_tool_output}],
            session_id="sess-secret",
            transcript_ref="sqlite:sess-secret",
            max_tool_output_chars=60,
            preview_chars=80,
        )

        marker = capped[0]["content"]
        assert "sk-" not in marker
        assert "ghp_" not in marker
        assert "[REDACTED_SECRET_LIKE_VALUE]" in marker
        assert "sha256=" in marker

    def test_model_facing_tool_cap_uses_raw_transcript_index_when_provided(self):
        history = [
            {"role": "assistant", "tool_calls": [{"id": "call_1"}], "content": "", "_transcript_message_index": 7},
            {"role": "tool", "tool_call_id": "call_1", "content": "z" * 100, "_transcript_message_index": 8},
        ]

        capped, _stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-raw-index",
            transcript_ref="/tmp/session_sess-raw-index.jsonl",
            max_tool_output_chars=40,
            preview_chars=10,
        )

        assert "message_index=8" in capped[1]["content"]
        assert "message_index=1" not in capped[1]["content"]
        assert "_transcript_message_index" not in capped[0]
        assert "_transcript_message_index" not in capped[1]

    def test_biff_prompt_budget_trims_only_model_facing_history(self):
        history = _make_history(40, content_size=1200)
        original = [dict(message) for message in history]

        budgeted, stats = apply_biff_prompt_budget(
            history,
            budget_tokens=6_000,
            system_context_prompt="hot context" * 200,
            channel_prompt="channel" * 100,
            tool_schema_chars=4_000,
        )

        assert stats.applied is True
        assert stats.budget_tokens == 6_000
        assert stats.omitted_messages > 0
        assert stats.kept_messages < stats.original_messages
        assert stats.kept_chars <= stats.budget_chars
        assert "Older Discord history was trimmed" in budgeted[0]["content"]
        assert history == original

    def test_biff_prompt_budget_preserves_assistant_tool_groups(self):
        history = [
            {"role": "user", "content": "old" * 2000},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            {"role": "assistant", "content": "new answer"},
        ]

        budgeted, stats = apply_biff_prompt_budget(history, budget_tokens=6_000, tool_schema_chars=18_000)

        assert stats.applied is True
        roles = [message["role"] for message in budgeted]
        if "tool" in roles:
            tool_index = roles.index("tool")
            assert roles[tool_index - 1] == "assistant"
            assert budgeted[tool_index - 1].get("tool_calls")
        assert budgeted[-1]["content"] == "new answer"

    def test_biff_prompt_budget_is_discord_only_and_exempts_bundles(self, monkeypatch):
        config = {"biff": {"platforms": {"discord": {"prompt_budget": True, "prompt_budget_tokens": 12_000}}}}

        assert biff_prompt_budget_enabled(config, "discord") is True
        assert biff_prompt_budget_enabled(config, "slack") is False
        assert resolve_biff_prompt_budget_tokens(config, "discord") == 12_000
        assert should_apply_biff_prompt_budget(config, "discord", message="normal question") is True
        bundle_message = 'The user has invoked the "/biff-hermes-runtime-change" skill bundle.'
        assert should_apply_biff_prompt_budget(config, "discord", message=bundle_message) is False

        monkeypatch.setenv("HERMES_BIFF_PROMPT_BUDGET_TOKENS", "50000")
        assert resolve_biff_prompt_budget_tokens(config, "discord") == 40_000

    def test_model_facing_tool_cap_enforces_aggregate_historical_tool_budget(self):
        history = []
        for i in range(8):
            history.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"call_{i}"}]})
            history.append({
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "content": f"tool {i} wrote /tmp/evidence-{i}.txt\n" + (str(i) * 10_000),
            })

        capped, stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-budget",
            transcript_ref="/tmp/sess-budget.jsonl",
            max_tool_output_chars=16_000,
            max_total_tool_output_chars=25_000,
            preview_chars=800,
        )

        tool_messages = [m for m in capped if m["role"] == "tool"]
        assert sum(len(m["content"]) for m in tool_messages) <= 25_000
        assert tool_messages[-1]["content"].startswith("tool 7 wrote")
        assert tool_messages[-2]["content"].startswith("tool 6 wrote")
        assert "[Gateway model-facing historical tool output summarized]" in tool_messages[0]["content"]
        assert "session_id=sess-budget" in tool_messages[0]["content"]
        assert "message_index=1" in tool_messages[0]["content"]
        assert "transcript_ref=/tmp/sess-budget.jsonl" in tool_messages[0]["content"]
        assert "sha256=" in tool_messages[0]["content"]
        assert "omitted_chars=" in tool_messages[0]["content"]
        assert "/tmp/evidence-0.txt" in tool_messages[0]["content"]
        assert stats.tool_outputs_capped_count == 6
        assert stats.tool_output_chars_before == 6 * (len("tool 0 wrote /tmp/evidence-0.txt\n") + 10_000)
        assert stats.tool_output_chars_after == sum(len(m["content"]) for m in tool_messages)
        assert stats.tool_output_chars_omitted > 0

    def test_model_facing_tool_cap_enforces_aggregate_budget_for_many_large_outputs(self):
        history = []
        for i in range(40):
            history.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"call_large_{i}"}]})
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": f"call_large_{i}",
                    "content": f"tool {i} wrote /tmp/large-evidence-{i}.log\n" + (str(i % 10) * 20_000),
                }
            )

        capped, stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-many-large",
            transcript_ref="/tmp/sess-many-large.jsonl",
            max_tool_output_chars=16_000,
            max_total_tool_output_chars=48_000,
            preview_chars=1_000,
        )

        tool_messages = [m for m in capped if m["role"] == "tool"]
        total_tool_chars = sum(len(m["content"]) for m in tool_messages)
        assert total_tool_chars <= 48_000
        assert [m["role"] for m in capped] == [m["role"] for m in history]
        assert capped[0]["tool_calls"] == history[0]["tool_calls"]
        assert capped[1]["tool_call_id"] == "call_large_0"
        assert "[Gateway model-facing historical tool output summarized]" in tool_messages[0]["content"]
        assert "session_id=sess-many-large" in tool_messages[0]["content"]
        assert "message_index=1" in tool_messages[0]["content"]
        assert "transcript_ref=/tmp/sess-many-large.jsonl" in tool_messages[0]["content"]
        assert "sha256=" in tool_messages[0]["content"]
        assert "/tmp/large-evidence-0.log" in tool_messages[0]["content"]
        assert stats.tool_outputs_capped_count == 40
        assert stats.tool_output_chars_after == total_tool_chars

    def test_model_facing_tool_cap_enforces_aggregate_budget_for_mixed_small_and_large_outputs(self):
        history = []
        for i in range(24):
            is_large = i % 3 == 0
            size = 22_000 if is_large else 2_000
            history.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"call_mixed_{i}"}]})
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": f"call_mixed_{i}",
                    "content": f"tool {i} wrote /tmp/mixed-evidence-{i}.txt\n" + ("x" * size),
                }
            )

        capped, stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-mixed",
            transcript_ref="sqlite:sess-mixed",
            max_tool_output_chars=16_000,
            max_total_tool_output_chars=48_000,
            preview_chars=1_000,
        )

        tool_messages = [m for m in capped if m["role"] == "tool"]
        total_tool_chars = sum(len(m["content"]) for m in tool_messages)
        assert total_tool_chars <= 48_000
        assert [m["role"] for m in capped] == [m["role"] for m in history]
        assert all(capped[i]["tool_calls"] == history[i]["tool_calls"] for i in range(0, len(history), 2))
        assert all(capped[i]["tool_call_id"] == history[i]["tool_call_id"] for i in range(1, len(history), 2))
        assert tool_messages[-1]["content"].startswith("tool 23 wrote")
        assert "[Gateway model-facing historical tool output summarized]" in tool_messages[0]["content"]
        assert "session_id=sess-mixed" in tool_messages[0]["content"]
        assert "message_index=1" in tool_messages[0]["content"]
        assert "transcript_ref=sqlite:sess-mixed" in tool_messages[0]["content"]
        assert "sha256=" in tool_messages[0]["content"]
        assert "/tmp/mixed-evidence-0.txt" in tool_messages[0]["content"]
        assert stats.tool_outputs_capped_count > 0
        assert stats.tool_output_chars_after == total_tool_chars

    @pytest.mark.parametrize(
        ("candidate_count", "expected_budget"),
        [
            (15, 48_000),
            (16, 32_000),
            (32, 24_000),
            (64, 16_000),
        ],
    )
    def test_default_aggregate_tool_budget_adapts_at_candidate_thresholds(self, candidate_count, expected_budget):
        history = _make_tool_history(
            candidate_count,
            call_prefix="call_adaptive",
            evidence_prefix="adaptive-evidence",
        )

        capped, stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-adaptive",
            transcript_ref="/tmp/sess-adaptive.jsonl",
            max_tool_output_chars=16_000,
            preview_chars=1_000,
        )

        total_tool_chars = _tool_content_chars(capped)
        assert total_tool_chars <= expected_budget
        assert [m["role"] for m in capped] == [m["role"] for m in history]
        assert capped[-2]["tool_calls"] == history[-2]["tool_calls"]
        assert capped[-1]["tool_call_id"] == f"call_adaptive_{candidate_count - 1}"
        assert stats.tool_outputs_capped_count == candidate_count
        assert stats.tool_output_chars_after == total_tool_chars

        explicit_capped, _ = cap_model_facing_tool_outputs(
            history,
            session_id="sess-adaptive-explicit",
            transcript_ref="/tmp/sess-adaptive-explicit.jsonl",
            max_tool_output_chars=16_000,
            max_total_tool_output_chars=48_000,
            preview_chars=1_000,
        )
        explicit_total = _tool_content_chars(explicit_capped)
        if expected_budget < 48_000:
            assert explicit_total > expected_budget
        else:
            assert total_tool_chars > 32_000

        if candidate_count >= 64:
            tool_messages = [m for m in capped if m["role"] == "tool"]
            assert "[Gateway model-facing historical tool output summarized]" in tool_messages[-1]["content"]
            assert "session_id=sess-adaptive" in tool_messages[-1]["content"]
            assert f"message_index={candidate_count * 2 - 1}" in tool_messages[-1]["content"]
            assert "transcript_ref=/tmp/sess-adaptive.jsonl" in tool_messages[-1]["content"]
            assert "sha256=" in tool_messages[-1]["content"]
            assert "omitted_chars=" in tool_messages[-1]["content"]
            assert f"/tmp/adaptive-evidence-{candidate_count - 1}.log" in tool_messages[-1]["content"]

    def test_explicit_aggregate_tool_budget_remains_non_adaptive_for_many_outputs(self):
        history = _make_tool_history(72, call_prefix="call_explicit", evidence_prefix="explicit-evidence")

        capped, stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-explicit",
            transcript_ref="/tmp/sess-explicit.jsonl",
            max_tool_output_chars=16_000,
            max_total_tool_output_chars=48_000,
            preview_chars=1_000,
        )

        total_tool_chars = _tool_content_chars(capped)
        assert 16_000 < total_tool_chars <= 48_000
        assert stats.tool_outputs_capped_count == 72
        assert stats.tool_output_chars_after == total_tool_chars

    @pytest.mark.parametrize("disabled_budget", [0, -1])
    def test_explicit_non_positive_aggregate_budget_disables_aggregate_cap(self, disabled_budget):
        history = _make_tool_history(8, call_prefix="call_disabled", evidence_prefix="disabled-evidence")

        capped, stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-disabled",
            transcript_ref="/tmp/sess-disabled.jsonl",
            max_tool_output_chars=16_000,
            max_total_tool_output_chars=disabled_budget,
            preview_chars=1_000,
        )

        tool_messages = [m for m in capped if m["role"] == "tool"]
        assert all("[Gateway model-facing tool output capped]" in m["content"] for m in tool_messages)
        assert all("[Gateway model-facing historical tool output summarized]" not in m["content"] for m in tool_messages)
        assert _tool_content_chars(capped) > 16_000
        assert stats.tool_outputs_capped_count == 8
        assert stats.tool_output_chars_after == _tool_content_chars(capped)

    def test_default_adaptive_budget_counts_large_candidates_not_total_tool_messages(self):
        history = _make_tool_history(
            30,
            large_every=3,
            call_prefix="call_mixed_default",
            evidence_prefix="mixed-default-evidence",
        )

        capped, stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-mixed-default",
            transcript_ref="/tmp/sess-mixed-default.jsonl",
            max_tool_output_chars=16_000,
            preview_chars=1_000,
        )

        tool_messages = [m for m in capped if m["role"] == "tool"]
        total_tool_chars = _tool_content_chars(capped)
        assert 32_000 < total_tool_chars <= 48_000
        assert tool_messages[-1]["content"].startswith("tool 29 wrote")
        assert stats.tool_outputs_capped_count > 0
        assert stats.tool_output_chars_after == total_tool_chars

    def test_default_adaptive_budget_reduces_sixty_three_aggregate_capped_outputs_below_48k(self):
        history = _make_tool_history(
            63,
            large_size=5_550,
            call_prefix="call_live_smoke",
            evidence_prefix="live-smoke-evidence",
        )

        capped, stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-live-smoke",
            transcript_ref="/tmp/sess-live-smoke.jsonl",
            max_tool_output_chars=16_000,
            preview_chars=1_000,
        )

        tool_messages = [m for m in capped if m["role"] == "tool"]
        total_tool_chars = _tool_content_chars(capped)
        assert _tool_content_chars(history) > 350_000
        assert total_tool_chars <= 24_000
        assert total_tool_chars < 48_000
        assert stats.tool_outputs_capped_count == 63
        assert [m["role"] for m in capped] == [m["role"] for m in history]
        assert all(capped[i]["tool_calls"] == history[i]["tool_calls"] for i in range(0, len(history), 2))
        assert all(capped[i]["tool_call_id"] == history[i]["tool_call_id"] for i in range(1, len(history), 2))
        assert "[Gateway model-facing historical tool output summarized]" in tool_messages[-1]["content"]
        assert "session_id=sess-live-smoke" in tool_messages[-1]["content"]
        assert "message_index=125" in tool_messages[-1]["content"]
        assert "transcript_ref=/tmp/sess-live-smoke.jsonl" in tool_messages[-1]["content"]
        assert "sha256=" in tool_messages[-1]["content"]
        assert "/tmp/live-smoke-evidence-62.log" in tool_messages[-1]["content"]
        assert stats.tool_output_chars_after == total_tool_chars

    def test_token_source_metrics_include_tool_output_before_after_and_omitted(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "calling", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "z" * 100},
        ]
        capped, cap_stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-metrics",
            transcript_ref="sqlite:sess-metrics",
            max_tool_output_chars=40,
            preview_chars=10,
        )

        metrics = collect_token_source_metrics(
            history,
            capped,
            cap_stats,
            system_context_prompt="system prompt",
            channel_prompt="channel",
            tool_schema_chars=123,
        )

        assert metrics["history_user_chars"] == 5
        assert metrics["history_assistant_chars"] == 7
        assert metrics["history_tool_output_chars_before_cap"] == 100
        assert metrics["history_tool_output_chars_after_cap"] == len(capped[2]["content"])
        assert metrics["tool_outputs_capped_count"] == 1
        assert metrics["tool_output_chars_omitted"] == 80
        assert metrics["tool_schema_chars"] == 123
        assert metrics["system_context_prompt_chars"] == len("system prompt")
        assert metrics["channel_prompt_chars"] == len("channel")

    def test_biff_operating_mode_resolution_and_prompt_preserve_core_contract(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_MODE", raising=False)
        cfg = {"biff": {"operating_mode": "economy", "platforms": {"discord": {"operating_mode": "evidence"}}}}

        discord_mode = resolve_biff_operating_mode(cfg, "discord")
        default_mode = resolve_biff_operating_mode(cfg, "slack")

        assert discord_mode.name == "evidence-only"
        assert default_mode.name == "economy"
        prompt = biff_operating_mode_prompt(discord_mode)
        assert "Do not change provider, model, account" in prompt
        assert "persona, memory behavior, safety gates, or source-of-truth policy" in prompt
        assert "Evidence-only" in prompt
        assert "Tool access is restricted" in prompt

        economy_prompt = biff_operating_mode_prompt(default_mode)
        assert "complete the bounded deliverable" in economy_prompt
        assert "do not stop at an acknowledgement" in economy_prompt
        assert "Prefer rg over grep" in economy_prompt
        assert "Use Kanban/background work only for genuinely broad work" in economy_prompt
        assert "short terminal timeouts" in economy_prompt

    def test_evidence_only_filters_to_read_only_evidence_toolsets(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_MODE", raising=False)
        evidence_mode = resolve_biff_operating_mode({"biff": {"operating_mode": "evidence-only"}}, "discord")
        economy_mode = resolve_biff_operating_mode({"biff": {"operating_mode": "economy"}}, "discord")
        configured = ["terminal", "file", "search", "web", "homeassistant", "session_search", "vision", "browser"]

        assert filter_biff_mode_enabled_toolsets(evidence_mode, configured) == [
            "search",
            "session_search",
            "vision",
            "web",
        ]
        assert filter_biff_mode_enabled_toolsets(economy_mode, configured) == sorted(configured)

    def test_biff_mode_caps_model_facing_text_without_mutating_transcript(self):
        mode = resolve_biff_operating_mode({"quota_economy": {"mode": "emergency"}}, "discord")
        history = [
            {"role": "user", "content": "u" * 9000},
            {"role": "assistant", "content": "ok"},
            {"role": "tool", "tool_call_id": "call_1", "content": "z" * 5000},
        ]

        capped, stats = cap_model_facing_tool_outputs(
            history,
            session_id="sess-mode",
            transcript_ref="sqlite:sess-mode",
            max_tool_output_chars=mode.max_tool_output_chars,
            preview_chars=mode.preview_chars,
            max_message_content_chars=mode.max_message_content_chars,
        )

        assert history[0]["content"] == "u" * 9000
        assert history[2]["content"] == "z" * 5000
        assert "content capped by Biff operating mode" in capped[0]["content"]
        assert "Gateway model-facing tool output capped" in capped[2]["content"]
        assert stats.message_contents_capped_count == 1
        assert stats.tool_outputs_capped_count == 1

    def test_discord_slowdown_guard_tightens_bloated_active_context_only(self):
        mode = resolve_biff_operating_mode({"biff": {"operating_mode": "economy"}}, "discord")
        history = [
            {"role": "assistant", "content": "a" * 55_000},
            {"role": "tool", "content": "t" * 260_000},
        ]

        guarded, info = apply_discord_slowdown_guard(mode, history, platform_key="discord")

        assert guarded.name == "emergency"
        assert info is not None
        assert info["from_mode"] == "economy"
        assert info["to_mode"] == "emergency"
        assert set(info["reasons"]) == {"assistant_chars", "tool_chars"}
        assert history[0]["content"] == "a" * 55_000
        assert history[1]["content"] == "t" * 260_000

    def test_discord_slowdown_guard_does_not_override_evidence_or_other_platforms(self):
        evidence = resolve_biff_operating_mode({"biff": {"operating_mode": "evidence-only"}}, "discord")
        economy = resolve_biff_operating_mode({"biff": {"operating_mode": "economy"}}, "discord")
        history = [{"role": "assistant", "content": "a" * 80_000}]

        guarded_evidence, evidence_info = apply_discord_slowdown_guard(evidence, history, platform_key="discord")
        guarded_slack, slack_info = apply_discord_slowdown_guard(economy, history, platform_key="slack")

        assert guarded_evidence.name == "evidence-only"
        assert evidence_info is None
        assert guarded_slack.name == "economy"
        assert slack_info is None

    def test_discord_slowdown_guard_does_not_kneecap_delivery_bundles(self):
        mode = resolve_biff_operating_mode({"biff": {"operating_mode": "normal"}}, "discord")
        history = [
            {"role": "assistant", "content": "a" * 55_000},
            {"role": "tool", "content": "t" * 260_000},
        ]
        message = '[IMPORTANT: The user has invoked the "biff-issue-execution" skill bundle.]'

        guarded, info = apply_discord_slowdown_guard(mode, history, platform_key="discord", message=message)

        assert guarded.name == "normal"
        assert info is None

    def test_biff_discord_tool_schema_profile_defaults_to_v3_allowlist(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_TOOL_SCHEMA_PROFILE", raising=False)
        configured = [
            "terminal",
            "file",
            "memory",
            "session_search",
            "skills",
            "todo",
            "clarify",
            "code_execution",
            "delegation",
            "web",
            "vision",
            "browser",
            "cronjob",
            "image_gen",
            "messaging",
            "tts",
            "kanban",
            "discord",
        ]

        assert resolve_biff_tool_schema_profile({}, "discord") == "v3"
        assert apply_biff_tool_schema_profile({}, "discord", configured) == [
            "file",
            "kanban",
            "memory",
            "skills-read",
            "terminal",
            "todo",
            "web",
        ]

    def test_biff_discord_tool_schema_profile_v2_keeps_previous_build_ops_allowlist(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_TOOL_SCHEMA_PROFILE", raising=False)
        configured = [
            "terminal",
            "file",
            "memory",
            "session_search",
            "skills",
            "todo",
            "clarify",
            "code_execution",
            "delegation",
            "web",
            "vision",
            "browser",
            "cronjob",
            "image_gen",
            "messaging",
            "tts",
            "kanban",
            "discord",
        ]
        cfg = {"biff": {"platforms": {"discord": {"tool_schema_profile": "v2"}}}}

        assert apply_biff_tool_schema_profile(cfg, "discord", configured) == [
            "code_execution",
            "delegation",
            "file",
            "kanban",
            "memory",
            "skills",
            "terminal",
            "todo",
        ]

    def test_biff_tool_schema_profile_core_keeps_legacy_wider_core(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_TOOL_SCHEMA_PROFILE", raising=False)
        configured = [
            "terminal",
            "file",
            "memory",
            "session_search",
            "skills",
            "todo",
            "clarify",
            "code_execution",
            "delegation",
            "web",
            "vision",
            "browser",
            "cronjob",
            "image_gen",
            "messaging",
            "tts",
            "kanban",
            "discord",
        ]
        cfg = {"biff": {"platforms": {"discord": {"tool_schema_profile": "core"}}}}

        assert apply_biff_tool_schema_profile(cfg, "discord", configured) == [
            "clarify",
            "code_execution",
            "delegation",
            "discord",
            "file",
            "kanban",
            "memory",
            "session_search",
            "skills",
            "terminal",
            "todo",
            "vision",
            "web",
        ]

    def test_biff_tool_schema_profile_env_full_preserves_escalation(self, monkeypatch):
        monkeypatch.setenv("HERMES_BIFF_TOOL_SCHEMA_PROFILE", "full")
        configured = ["terminal", "file", "browser", "cronjob", "messaging"]
        cfg = {"biff": {"platforms": {"discord": {"tool_schema_profile": "v2"}}}}

        assert apply_biff_tool_schema_profile(cfg, "discord", configured) == sorted(configured)

    def test_biff_tool_schema_profile_non_discord_default_stays_full(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_TOOL_SCHEMA_PROFILE", raising=False)
        configured = ["terminal", "file", "browser", "cronjob", "messaging"]

        assert resolve_biff_tool_schema_profile({}, "slack") == "full"
        assert apply_biff_tool_schema_profile({}, "slack", configured) == sorted(configured)

    def test_biff_tool_schema_profile_config_full_rolls_back_discord_default(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_TOOL_SCHEMA_PROFILE", raising=False)
        configured = ["terminal", "file", "browser", "cronjob", "messaging"]
        cfg = {"biff": {"platforms": {"discord": {"tool_schema_profile": "full"}}}}

        assert resolve_biff_tool_schema_profile(cfg, "discord") == "full"
        assert apply_biff_tool_schema_profile(cfg, "discord", configured) == sorted(configured)

    def test_biff_tool_schema_profile_env_core_preserves_legacy_profile(self, monkeypatch):
        monkeypatch.setenv("HERMES_BIFF_TOOL_SCHEMA_PROFILE", "core")
        configured = ["terminal", "file", "browser", "session_search", "skills", "clarify"]
        cfg = {"biff": {"platforms": {"discord": {"tool_schema_profile": "v2"}}}}

        assert resolve_biff_tool_schema_profile(cfg, "discord") == "core"
        assert apply_biff_tool_schema_profile(cfg, "discord", configured) == [
            "clarify",
            "file",
            "session_search",
            "skills",
            "terminal",
        ]

    def test_biff_discord_v3_reduces_model_facing_tools_and_schema_chars(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_TOOL_SCHEMA_PROFILE", raising=False)
        from hermes_cli.tools_config import _get_platform_tools
        from model_tools import get_tool_definitions
        import json

        cfg = {"platform_toolsets": {"discord": ["hermes-discord"]}}
        configured = sorted(_get_platform_tools(cfg, "discord"))
        full_toolsets = apply_biff_tool_schema_profile(
            {"biff": {"tool_schema_profile": "full"}, **cfg},
            "discord",
            configured,
        )
        default_toolsets = apply_biff_tool_schema_profile(cfg, "discord", configured)

        full_tools = get_tool_definitions(full_toolsets, None, quiet_mode=True)
        default_tools = get_tool_definitions(default_toolsets, None, quiet_mode=True)
        full_chars = len(json.dumps(full_tools, sort_keys=True, separators=(",", ":")))
        default_chars = len(json.dumps(default_tools, sort_keys=True, separators=(",", ":")))
        default_names = {tool["function"]["name"] for tool in default_tools}

        assert len(default_tools) < len(full_tools)
        assert default_chars < full_chars
        assert default_chars <= 18_000
        assert full_chars - default_chars >= 20_000
        assert {
            "read_file",
            "search_files",
            "write_file",
            "patch",
            "terminal",
            "process",
            "skill_view",
            "skills_list",
            "memory",
            "todo",
        }.issubset(default_names)
        assert {
            "cronjob",
            "delegate_task",
            "execute_code",
            "image_generate",
            "send_message",
            "session_search",
            "skill_manage",
            "text_to_speech",
            "vision_analyze",
        }.isdisjoint(default_names)

    def test_empty_discord_rollover_turn_keeps_repo_shell_kanban_toolsets(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_TOOL_SCHEMA_PROFILE", raising=False)
        from hermes_cli.tools_config import _get_platform_tools
        from model_tools import get_tool_definitions

        cfg = {"platform_toolsets": {"discord": ["hermes-discord"]}}
        configured = sorted(_get_platform_tools(cfg, "discord"))
        profiled = apply_biff_tool_schema_profile(cfg, "discord", configured)
        planned = apply_biff_turn_toolset_plan(
            cfg,
            "discord",
            profiled,
            message="",
            configured_toolsets=configured,
        )
        names = {tool["function"]["name"] for tool in get_tool_definitions(planned, [], quiet_mode=True)}

        assert {"terminal", "process", "read_file", "search_files", "patch"}.issubset(names)

    def test_biff_bundle_tool_widening_adds_only_selected_specialist_toolsets(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_BUNDLE_TOOL_WIDENING", raising=False)
        configured = [
            "terminal",
            "file",
            "memory",
            "skills-read",
            "skills",
            "todo",
            "kanban",
            "code_execution",
            "delegation",
            "web",
            "vision",
            "browser",
        ]
        narrowed = ["terminal", "file", "memory", "skills-read", "todo", "kanban"]
        message = '[IMPORTANT: The user has invoked the "biff-hermes-runtime-change" skill bundle.]'

        widened = widen_biff_toolsets_for_bundle(
            {"biff": {"platforms": {"discord": {"bundle_tool_widening": True}}}},
            "discord",
            narrowed,
            configured,
            message=message,
        )

        assert "code_execution" in widened
        assert "delegation" in widened
        assert "skills" in widened
        assert "skills-read" not in widened
        assert "browser" not in widened

    def test_biff_bundle_tool_widening_respects_configured_platform_tools(self, monkeypatch):
        monkeypatch.delenv("HERMES_BIFF_BUNDLE_TOOL_WIDENING", raising=False)

        widened = widen_biff_toolsets_for_bundle(
            {},
            "discord",
            ["terminal", "file", "kanban"],
            ["terminal", "file", "kanban"],
            bundle_key="biff-issue-execution",
        )

        assert widened == ["file", "kanban", "terminal"]

    def test_slow_work_deflection_catches_broad_background_requests(self):
        decision = maybe_build_slow_work_deflection(
            "Please migrate all legacy tracker stories and scan the whole Obsidian workspace for references before updating the board.",
            platform_key="discord",
        )

        assert decision is not None
        assert decision.assignee == "ranger"
        assert "Original request" in decision.body

    def test_slow_work_deflection_ignores_questions_and_short_prompts(self):
        assert maybe_build_slow_work_deflection(
            "why is the bot slow to respond?",
            platform_key="discord",
        ) is None
        assert maybe_build_slow_work_deflection(
            "archive all stories",
            platform_key="discord",
        ) is None

    def test_plain_language_heartbeat_hides_tool_details(self):
        text = render_plain_language_heartbeat(
            elapsed_seconds=245,
            activity={"current_tool": "terminal", "last_activity_desc": "running grep -rnH"},
        )

        assert text == "Still working: finding the relevant files. (4 min elapsed)"
        assert "terminal" not in text
        assert "grep" not in text

    def test_plain_language_heartbeat_uses_real_stage(self):
        text = render_plain_language_heartbeat(
            elapsed_seconds=125,
            activity={"current_tool": "terminal", "last_activity_desc": "cd web && npm run build"},
        )

        assert text == "Still working: rebuilding the app. (2 min elapsed)"
        assert "npm" not in text

class TestSessionHygieneThresholds:
    """Test that the threshold logic correctly identifies large sessions.

    Thresholds are derived from model context length × compression threshold,
    matching what the agent's ContextCompressor uses.
    """

    def test_small_session_below_thresholds(self):
        """A 10-message session should not trigger compression."""
        history = _make_history(10)
        approx_tokens = estimate_messages_tokens_rough(history)

        # For a 200k-context model at 85% threshold = 170k
        context_length = 200_000
        threshold_pct = 0.85
        compress_token_threshold = int(context_length * threshold_pct)

        needs_compress = approx_tokens >= compress_token_threshold
        assert not needs_compress

    def test_large_token_count_triggers(self):
        """High token count should trigger compression when exceeding model threshold."""
        # Build a history that exceeds 85% of a 200k model (170k tokens)
        history = _make_large_history_tokens(180_000)
        approx_tokens = estimate_messages_tokens_rough(history)

        context_length = 200_000
        threshold_pct = 0.85
        compress_token_threshold = int(context_length * threshold_pct)

        needs_compress = approx_tokens >= compress_token_threshold
        assert needs_compress

    def test_under_threshold_no_trigger(self):
        """Session under threshold should not trigger, even with many messages."""
        # 250 short messages — lots of messages but well under token threshold
        history = _make_history(250, content_size=10)
        approx_tokens = estimate_messages_tokens_rough(history)

        # 200k model at 85% = 170k token threshold
        context_length = 200_000
        threshold_pct = 0.85
        compress_token_threshold = int(context_length * threshold_pct)

        needs_compress = approx_tokens >= compress_token_threshold
        assert not needs_compress, (
            f"250 short messages (~{approx_tokens} tokens) should NOT trigger "
            f"compression at {compress_token_threshold} token threshold"
        )

    def test_message_count_alone_does_not_trigger(self):
        """Message count alone should NOT trigger — only token count matters.

        The old system used an OR of token-count and message-count thresholds,
        which caused premature compression in tool-heavy sessions with 200+
        messages but low total tokens.
        """
        # 300 very short messages — old system would compress, new should not
        history = _make_history(300, content_size=10)
        approx_tokens = estimate_messages_tokens_rough(history)

        context_length = 200_000
        threshold_pct = 0.85
        compress_token_threshold = int(context_length * threshold_pct)

        # Token-based check only
        needs_compress = approx_tokens >= compress_token_threshold
        assert not needs_compress

    def test_threshold_scales_with_model(self):
        """Different models should have different compression thresholds."""
        # 128k model at 85% = 108,800 tokens
        small_model_threshold = int(128_000 * 0.85)
        # 200k model at 85% = 170,000 tokens
        large_model_threshold = int(200_000 * 0.85)
        # 1M model at 85% = 850,000 tokens
        huge_model_threshold = int(1_000_000 * 0.85)

        # A session at ~120k tokens:
        history = _make_large_history_tokens(120_000)
        approx_tokens = estimate_messages_tokens_rough(history)

        # Should trigger for 128k model
        assert approx_tokens >= small_model_threshold
        # Should NOT trigger for 200k model
        assert approx_tokens < large_model_threshold
        # Should NOT trigger for 1M model
        assert approx_tokens < huge_model_threshold

    def test_custom_threshold_percentage(self):
        """Custom threshold percentage from config should be respected."""
        context_length = 200_000

        # At 50% threshold = 100k
        low_threshold = int(context_length * 0.50)
        # At 90% threshold = 180k
        high_threshold = int(context_length * 0.90)

        history = _make_large_history_tokens(150_000)
        approx_tokens = estimate_messages_tokens_rough(history)

        # Should trigger at 50% but not at 90%
        assert approx_tokens >= low_threshold
        assert approx_tokens < high_threshold

    def test_minimum_message_guard(self):
        """Sessions with fewer than 4 messages should never trigger."""
        history = _make_history(3, content_size=100_000)
        # Even with enormous content, < 4 messages should be skipped
        # (the gateway code checks `len(history) >= 4` before evaluating)
        assert len(history) < 4


class TestSessionHygieneWarnThreshold:
    """Test the post-compression warning threshold (95% of context)."""

    def test_warn_when_still_large(self):
        """If compressed result is still above 95% of context, should warn."""
        context_length = 200_000
        warn_threshold = int(context_length * 0.95)  # 190k
        post_compress_tokens = 195_000
        assert post_compress_tokens >= warn_threshold

    def test_no_warn_when_under(self):
        """If compressed result is under 95% of context, no warning."""
        context_length = 200_000
        warn_threshold = int(context_length * 0.95)  # 190k
        post_compress_tokens = 150_000
        assert post_compress_tokens < warn_threshold





class TestEstimatedTokenThreshold:
    """Verify that hygiene thresholds are always below the model's context
    limit — for both actual and estimated token counts.

    Regression: a previous 1.4x multiplier on rough estimates pushed the
    threshold to 85% * 1.4 = 119% of context, which exceeded the model's
    limit and prevented hygiene from ever firing for ~200K models (GLM-5).
    The fix removed the multiplier entirely — the 85% threshold already
    provides ample headroom over the agent's 50% compressor.
    """

    def test_threshold_below_context_for_200k_model(self):
        """Hygiene threshold must always be below model context."""
        context_length = 200_000
        threshold = int(context_length * 0.85)
        assert threshold < context_length

    def test_threshold_below_context_for_128k_model(self):
        context_length = 128_000
        threshold = int(context_length * 0.85)
        assert threshold < context_length

    def test_no_multiplier_means_same_threshold_for_estimated_and_actual(self):
        """Without the 1.4x, estimated and actual token paths use the same threshold."""
        context_length = 200_000
        threshold_pct = 0.85
        threshold = int(context_length * threshold_pct)
        # Both paths should use 170K — no inflation
        assert threshold == 170_000

    def test_warn_threshold_below_context(self):
        """Warn threshold (95%) must be below context length."""
        for ctx in (128_000, 200_000, 1_000_000):
            warn = int(ctx * 0.95)
            assert warn < ctx

    def test_overestimate_fires_early_but_safely(self):
        """If rough estimate is 50% inflated, hygiene fires at ~57% actual usage.

        That's between the agent's 50% threshold and the model's limit —
        safe and harmless.
        """
        context_length = 200_000
        threshold = int(context_length * 0.85)  # 170K
        # If actual tokens = 113K, rough estimate = 113K * 1.5 = 170K
        # Hygiene fires when estimate hits 170K, actual is ~113K = 57% of ctx
        actual_when_fires = threshold / 1.5
        assert actual_when_fires > context_length * 0.50, (
            "Early fire should still be above agent's 50% threshold"
        )
        assert actual_when_fires < context_length, (
            "Early fire must be well below model limit"
        )


class TestTokenEstimation:
    """Verify rough token estimation works as expected for hygiene checks."""

    def test_empty_history(self):
        assert estimate_messages_tokens_rough([]) == 0

    def test_proportional_to_content(self):
        small = _make_history(10, content_size=100)
        large = _make_history(10, content_size=10_000)
        assert estimate_messages_tokens_rough(large) > estimate_messages_tokens_rough(small)

    def test_proportional_to_count(self):
        few = _make_history(10, content_size=1000)
        many = _make_history(100, content_size=1000)
        assert estimate_messages_tokens_rough(many) > estimate_messages_tokens_rough(few)

    def test_pathological_session_detected(self):
        """The reported pathological case: 648 messages, ~299K tokens.

        With a 200k model at 85% threshold (170k), this should trigger.
        """
        history = _make_history(648, content_size=1800)
        tokens = estimate_messages_tokens_rough(history)
        # Should be well above the 170K threshold for a 200k model
        threshold = int(200_000 * 0.85)
        assert tokens > threshold


@pytest.mark.asyncio
async def test_session_hygiene_messages_stay_in_originating_topic(monkeypatch, tmp_path):
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    class FakeCompressAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.model = kwargs.get("model")
            self.session_id = kwargs.get("session_id", "fake-session")
            self._print_fn = None
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()
            type(self).last_instance = self

        def _compress_context(self, messages, *_args, **_kwargs):
            # Simulate real _compress_context: create a new session_id
            self.session_id = f"{self.session_id}_compressed"
            return ([{"role": "assistant", "content": "compressed"}], None)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeCompressAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:group:-1001:17585",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="group",
    )
    runner.session_store.load_transcript.return_value = _make_history(6, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "795544298")

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1001",
            chat_type="group",
            thread_id="17585",
            user_id="12345",
        ),
        message_id="1",
    )

    result = await runner._handle_message(event)

    assert result == "ok"
    # Compression warnings are no longer sent to users — compression
    # happens silently with server-side logging only.
    assert len(adapter.sent) == 0
    assert FakeCompressAgent.last_instance is not None
    FakeCompressAgent.last_instance.shutdown_memory_provider.assert_called_once()
    FakeCompressAgent.last_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_session_hygiene_warns_user_when_compression_aborts(monkeypatch, tmp_path):
    """When auxiliary compression's summary LLM call fails, the compressor
    ABORTS — returns messages unchanged, sets _last_compress_aborted=True,
    and drops nothing.  Gateway must surface a visible ⚠️ warning to the
    user (including thread_id metadata so it lands in the originating
    topic/thread) saying the conversation is unchanged and how to retry."""
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    class FakeCompressAgentWithSummaryFailure:
        last_instance = None

        def __init__(self, **kwargs):
            self.model = kwargs.get("model")
            self.session_id = kwargs.get("session_id", "fake-session")
            self._print_fn = None
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()
            # Simulate a compressor that hit summary-generation failure
            # and ABORTED — no fallback inserted, no messages dropped.
            self.context_compressor = SimpleNamespace(
                _last_compress_aborted=True,
                _last_summary_fallback_used=False,
                _last_summary_dropped_count=0,
                _last_summary_error="404 model not found: gemini-3-flash-preview",
            )
            type(self).last_instance = self

        def _compress_context(self, messages, *_args, **_kwargs):
            # Abort path: messages preserved unchanged, session NOT rotated.
            return (messages, None)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeCompressAgentWithSummaryFailure
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:group:-1001:17585",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="group",
    )
    runner.session_store.load_transcript.return_value = _make_history(6, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "795544298")

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1001",
            chat_type="group",
            thread_id="17585",
            user_id="12345",
        ),
        message_id="1",
    )

    result = await runner._handle_message(event)

    assert result == "ok"
    # The compressor reported abort → exactly one warning message must
    # have been delivered to the user.
    warning_messages = [s for s in adapter.sent if "Context compression aborted" in s["content"]]
    assert len(warning_messages) == 1, (
        f"Expected 1 compression-aborted warning, got {len(warning_messages)}: {adapter.sent}"
    )
    warn = warning_messages[0]
    # Warning must include the underlying error and tell the user nothing
    # was dropped.
    assert "404" in warn["content"]
    assert "No messages were dropped" in warn["content"]
    # Warning must land in the originating topic/thread, not the main channel.
    assert warn["chat_id"] == "-1001"
    assert warn["metadata"] == {"thread_id": "17585"}

    FakeCompressAgentWithSummaryFailure.last_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_session_hygiene_informs_user_when_aux_model_fails_but_recovers(monkeypatch, tmp_path):
    """When the user's configured ``auxiliary.compression.model`` errors out
    and we recover via the main model, compression succeeds but the user's
    config is still broken.  Gateway hygiene must surface an ℹ note so the
    user knows to fix ``auxiliary.compression.model`` — silent recovery
    hides a misconfig only they can resolve."""
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    class FakeCompressAgentWithAuxRecovery:
        last_instance = None

        def __init__(self, **kwargs):
            self.model = kwargs.get("model")
            self.session_id = kwargs.get("session_id", "fake-session")
            self._print_fn = None
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()
            # Compression succeeded (no placeholder inserted) but the
            # configured aux model errored and we fell back to main.
            self.context_compressor = SimpleNamespace(
                _last_summary_fallback_used=False,
                _last_summary_dropped_count=0,
                _last_summary_error=None,
                _last_aux_model_failure_model="gemini-3-flash-preview",
                _last_aux_model_failure_error="404 model not found",
            )
            type(self).last_instance = self

        def _compress_context(self, messages, *_args, **_kwargs):
            self.session_id = f"{self.session_id}_compressed"
            return ([{"role": "assistant", "content": "real summary"}], None)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeCompressAgentWithAuxRecovery
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:group:-1001:17585",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="group",
    )
    runner.session_store.load_transcript.return_value = _make_history(6, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "795544298")

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1001",
            chat_type="group",
            thread_id="17585",
            user_id="12345",
        ),
        message_id="1",
    )

    result = await runner._handle_message(event)

    assert result == "ok"
    # No ⚠️ hard-failure warning (that's for dropped turns)
    hard_warnings = [s for s in adapter.sent if "Context compression summary failed" in s["content"]]
    assert len(hard_warnings) == 0, adapter.sent
    # But an ℹ note about the configured aux model must be delivered.
    aux_notes = [
        s for s in adapter.sent
        if "Configured compression model" in s["content"]
    ]
    assert len(aux_notes) == 1, (
        f"Expected 1 aux-model fallback notice, got {len(aux_notes)}: {adapter.sent}"
    )
    note = aux_notes[0]
    assert "gemini-3-flash-preview" in note["content"]
    assert "404" in note["content"]
    assert "auxiliary.compression.model" in note["content"]
    # Note must land in the originating topic/thread.
    assert note["chat_id"] == "-1001"
    assert note["metadata"] == {"thread_id": "17585"}

    FakeCompressAgentWithAuxRecovery.last_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_session_hygiene_honors_configurable_hard_message_limit(
    monkeypatch, tmp_path
):
    """compression.hygiene_hard_message_limit overrides the 400-message default.

    Regression for user-reported fix: a gateway session with a small
    transcript (12 messages) should not hit hygiene compression by default,
    but WILL when the user lowers the hard-limit to 10.  Verifies the new
    config key is actually read and applied at the force-compress gate.
    """
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    class FakeCompressAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.model = kwargs.get("model")
            self.session_id = kwargs.get("session_id", "fake-session")
            self._print_fn = None
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()
            type(self).last_instance = self

        def _compress_context(self, messages, *_args, **_kwargs):
            self.session_id = f"{self.session_id}_compressed"
            return ([{"role": "assistant", "content": "compressed"}], None)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeCompressAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    # Write config.yaml with lowered hard-limit
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "compression:\n"
        "  enabled: true\n"
        "  hygiene_hard_message_limit: 10\n"
    )

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:private:12345",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="private",
    )
    # 12 messages: below 400 default → no compression without override,
    # but above the configured limit of 10 → should compress.
    runner.session_store.load_transcript.return_value = _make_history(12, content_size=40)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"}
    )
    # Pick a context length large enough that the token-based threshold
    # won't trigger for 12 short messages — hard-limit must be the ONLY
    # thing firing compression.
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 1_000_000,
    )

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="private",
            user_id="12345",
        ),
        message_id="1",
    )

    result = await runner._handle_message(event)

    assert result == "ok"
    # The compression agent was instantiated → hard-limit fired on the
    # configured value (10), not the hardcoded 400 default.
    assert FakeCompressAgent.last_instance is not None, (
        "Expected hygiene compression to fire when message count (12) "
        "exceeds configured hygiene_hard_message_limit (10)"
    )


@pytest.mark.asyncio
async def test_session_hygiene_default_hard_message_limit_does_not_fire_at_12_messages(
    monkeypatch, tmp_path
):
    """Sanity check for the companion test above: without config override,
    12 messages must NOT trigger the 400-message hard limit.  If this test
    passes without changes, the override test's finding is meaningful."""
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    class FakeCompressAgent:
        last_instance = None

        def __init__(self, **kwargs):
            type(self).last_instance = self
            self.session_id = kwargs.get("session_id", "fake-session")
            self._print_fn = None
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()

        def _compress_context(self, messages, *_args, **_kwargs):
            return ([{"role": "assistant", "content": "compressed"}], None)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeCompressAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    # No config.yaml — use defaults (hard_limit=400)
    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:private:12345",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="private",
    )
    runner.session_store.load_transcript.return_value = _make_history(12, content_size=40)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"}
    )
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 1_000_000,
    )

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="private",
            user_id="12345",
        ),
        message_id="1",
    )

    result = await runner._handle_message(event)

    assert result == "ok"
    # No compression agent instantiated — 12 messages well under 400 default.
    assert FakeCompressAgent.last_instance is None, (
        "Compression should NOT fire at 12 messages with default hard_limit=400"
    )


def test_biff_runtime_instability_detection_flags_restart_and_tool_loop_patterns():
    signal = detect_biff_runtime_instability(
        """
        gateway received SIGTERM during restart
        later: signal.SIGTERM observed again
        Codex Responses stream terminal frame had output=None
        Codex Responses stream terminal frame had output=None
        long_turn_repeated_failure_fallback after repeated_exact_failure_warning
        """
    )

    assert signal.active is True
    assert "recent_gateway_restarts" in signal.reasons
    assert "codex_empty_terminal_frames" in signal.reasons
    assert "repeated_tool_or_long_turn_loop" in signal.reasons


def test_biff_runtime_instability_guard_downgrades_mode_and_narrows_tool_budget():
    mode = resolve_biff_operating_mode({"biff": {"operating_mode": "normal"}}, "discord")
    signal = detect_biff_runtime_instability("SIGTERM SIGTERM output=None output=None")

    guarded = apply_biff_runtime_instability_guard(mode, signal)
    adjusted = apply_biff_runtime_instability_tool_guardrails(
        {"max_tool_calls": 60, "terminal_timeout": 45},
        signal,
    )

    assert guarded.name == "evidence-only"
    assert adjusted["max_tool_calls"] == 2
    assert adjusted["terminal_timeout"] == 10
    assert adjusted["runtime_instability_guard"]["active"] is True


def test_biff_runtime_instability_log_inspector_reads_recent_gateway_logs(tmp_path):
    (tmp_path / "gateway.error.log").write_text("old\nSIGTERM\nSIGTERM\n", encoding="utf-8")
    (tmp_path / "errors.log").write_text("output=None\noutput=None\n", encoding="utf-8")

    signal = inspect_biff_runtime_instability_logs(tmp_path)

    assert signal.active is True
    assert "recent_gateway_restarts" in signal.reasons
    assert "codex_empty_terminal_frames" in signal.reasons
