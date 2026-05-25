import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource


RUN_PY = gateway_run.Path(__file__).resolve().parents[2] / "gateway" / "run.py"


class _RunningAgent:
    def __init__(self) -> None:
        self.steered: list[str] = []
        self.interrupted: list[str] = []

    def steer(self, text: str) -> bool:
        self.steered.append(text)
        return True

    def interrupt(self, text: str) -> None:
        self.interrupted.append(text)

    def get_activity_summary(self) -> dict[str, Any]:
        return {"api_call_count": 1, "max_iterations": 4, "current_tool": "synthetic"}


def _runner_with_busy_adapter(mode: str = "queue") -> tuple[GatewayRunner, Any, MessageEvent, str, _RunningAgent]:
    runner = GatewayRunner(GatewayConfig())
    runner._busy_input_mode = mode
    runner._is_user_authorized = lambda source: True
    adapter = SimpleNamespace(_pending_messages={}, _send_with_retry=AsyncMock())
    runner.adapters[Platform.DISCORD] = cast(Any, adapter)
    source = SessionSource(platform=Platform.DISCORD, chat_id="current-chat", user_id="marco")
    event = MessageEvent(text="follow-up", source=source, message_id="msg-1")
    session_key = runner._session_key_for_source(source)
    running_agent = _RunningAgent()
    runner._running_agents[session_key] = running_agent
    runner._running_agents_ts[session_key] = gateway_run.time.time()
    return runner, adapter, event, session_key, running_agent


@pytest.mark.asyncio
async def test_busy_queue_merge_failure_degrades_without_blocking_live_chat(monkeypatch):
    runner, adapter, event, session_key, running_agent = _runner_with_busy_adapter("queue")

    def boom(*_args, **_kwargs):
        raise RuntimeError("synthetic queue store failure")

    monkeypatch.setattr(gateway_run, "merge_pending_message_event", boom)

    handled = await runner._handle_active_session_busy_message(event, session_key)

    assert handled is True
    assert adapter._send_with_retry.await_count == 1
    sent = adapter._send_with_retry.await_args.kwargs
    assert sent["chat_id"] == "current-chat"
    assert "could not queue that follow-up" in sent["content"]
    assert "please resend after the current task finishes" in sent["content"]
    assert "Queued for the next turn" not in sent["content"]
    assert running_agent.interrupted == []


@pytest.mark.asyncio
async def test_busy_steer_injects_without_replaying_as_queued_message():
    runner, adapter, event, session_key, running_agent = _runner_with_busy_adapter("steer")
    event.text = "/steer use the safer path"

    handled = await runner._handle_active_session_busy_message(event, session_key)

    assert handled is True
    assert running_agent.steered == ["/steer use the safer path"]
    assert adapter._pending_messages == {}
    sent = adapter._send_with_retry.await_args.kwargs
    assert "Steered into current run" in sent["content"]


@pytest.mark.asyncio
async def test_drain_busy_ack_failure_is_handled_as_noop():
    runner, adapter, event, session_key, _running_agent = _runner_with_busy_adapter("queue")
    runner._draining = True
    adapter._send_with_retry.side_effect = RuntimeError("synthetic discord send outage")

    handled = await runner._handle_active_session_busy_message(event, session_key)

    assert handled is True
    assert adapter._send_with_retry.await_count == 1


def test_discord_current_chat_reply_metadata_does_not_force_thread_by_default():
    runner = GatewayRunner(GatewayConfig())
    source = SessionSource(platform=Platform.DISCORD, chat_id="current-chat", user_id="marco")
    event = MessageEvent(text="hi", source=source, message_id="msg-123")

    assert runner._thread_metadata_for_source(source, runner._reply_anchor_for_event(event)) is None
    assert runner._reply_anchor_for_event(event) == "msg-123"


def test_nonblocking_specialist_dispatch_has_fail_closed_degraded_notice():
    source = RUN_PY.read_text()

    assert "biff_nonblocking_specialist_dispatch_blocked" in source
    assert "dispatch is temporarily degraded, so I did not run this live" in source
    assert "Biff can retry or put it on Kanban" in source


def test_update_prompt_fallback_send_failure_is_bounded():
    source = RUN_PY.read_text()
    block = source[source.index("Update prompt fallback send failed") - 500:source.index("Update prompt fallback send failed") + 180]

    assert "except Exception as send_err" in block
    assert "continue" in block


def test_loop_bounding_synthetic_scenarios_cover_empty_or_broad_searches():
    from gateway.biff_synthetic_checks import DEFAULT_SCENARIOS

    scenarios = {scenario.name: scenario for scenario in DEFAULT_SCENARIOS}
    assert scenarios["explicit_secondbrain_lookup"].max_tool_calls == 1
    assert scenarios["smart_connections_fallback_to_bounded_lookup"].max_tool_calls == 1
    assert scenarios["broad_secondbrain_deep_research_background"].expected_background is True
    assert scenarios["broad_multi_system_verification_background"].expected_background is True
    assert scenarios["refresh_resume_context_recovery"].max_tool_calls == 3


def test_operator_note_exists_for_gateway_queue_update_changes():
    note = RUN_PY.parents[1] / "docs" / "biff-runtime-guardrails-operator-note.md"
    text = note.read_text()

    assert "Rollback:" in text
    assert "/Library/LaunchDaemons/ai.hermes.gateway.plist" in text
    assert "sudo launchctl bootout system /Library/LaunchDaemons/ai.hermes.gateway.plist" in text
    assert "sudo launchctl bootstrap system /Library/LaunchDaemons/ai.hermes.gateway.plist" in text
    assert "launchctl print system/ai.hermes.gateway" in text
    assert "hermes gateway status" in text
    assert "Do not run ad-hoc/concurrent gateway sessions" in text
    assert "busy/drain queue handling" in text
