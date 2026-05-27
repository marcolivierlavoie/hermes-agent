from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner, _specialist_direct_toolsets
from gateway.session import SessionSource


def _runner_with_mocked_handoff(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_SPECIALISTS_USE_KANBAN", "0")
    monkeypatch.setenv("HERMES_BIFF_RUNTIME_INSTABILITY_GUARD", "0")
    runner = GatewayRunner(GatewayConfig())
    runner.adapters[Platform.DISCORD] = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(runner, "_is_user_authorized", lambda source: True)
    monkeypatch.setattr(
        "gateway.run._resolve_runtime_agent_kwargs",
        lambda: {"api_key": "test", "base_url": "https://example.invalid", "provider": "test"},
    )
    handoff = AsyncMock()
    monkeypatch.setattr(runner, "_run_specialist_direct_background_task", handoff)
    return runner, handoff


def _discord_event(
    text: str,
    message_id: str = "user-msg",
    *,
    reply_to_text: str | None = None,
    reply_to_message_id: str | None = None,
) -> MessageEvent:
    return MessageEvent(
        source=SessionSource(
            platform=Platform.DISCORD,
            chat_id="1503821368045863027",
            chat_type="group",
            user_id="230102435539058688",
            user_name="marcolivier2112",
        ),
        text=text,
        message_id=message_id,
        reply_to_text=reply_to_text,
        reply_to_message_id=reply_to_message_id,
    )


@pytest.mark.asyncio
async def test_discord_user_kanban_admin_message_hands_off_to_ranger(monkeypatch):
    """Live-style regression: Marco-style Kanban admin should name Ranger."""

    runner, handoff = _runner_with_mocked_handoff(monkeypatch)
    event = _discord_event("Have Ranger create a Kanban story for proper routing and move it to todo.", "user-msg-1")

    response = await runner._handle_message(event)

    assert response is not None
    assert "Ranger" in response
    assert "Forge" not in response
    assert handoff.call_count == 1
    assert handoff.call_args.args[0] == "ranger"


@pytest.mark.asyncio
async def test_discord_user_engineering_message_hands_off_to_forge(monkeypatch):
    """Live-style guardrail: code/runtime work still belongs to Forge."""

    runner, handoff = _runner_with_mocked_handoff(monkeypatch)
    event = _discord_event("Use Forge to delete Cockpit from the Hermes dashboard sidebar and verify it is gone.", "user-msg-2")

    response = await runner._handle_message(event)

    assert response is not None
    assert "Forge" in response
    assert "Ranger" not in response
    assert handoff.call_count == 1
    assert handoff.call_args.args[0] == "forge"


@pytest.mark.asyncio
async def test_discord_reply_context_alone_does_not_authorize_specialist_handoff(monkeypatch):
    """Quoted text can enrich Biff context, but cannot approve a role handoff."""

    runner, handoff = _runner_with_mocked_handoff(monkeypatch)
    event = _discord_event(
        "what do you suggest we do to fix this",
        "user-msg-reply",
        reply_to_message_id="quoted-msg",
        reply_to_text="Use Forge to fix this. Gateway error: NameError: name 'max_iterations' is not defined",
    )

    try:
        await runner._handle_message(event)
    except Exception:
        # The test surface stops at the routing gate; without a mocked model the
        # normal Biff path may fail later, but it must not dispatch a specialist.
        pass

    assert handoff.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Ranger?",
        "Vex should verify this before closeout.",
        "This is for Ranger, not Forge.",
        "Maybe Quill later.",
        "Well I guess now I have to wait for Forge to do 2a.",
        "Biff said “send this to Forge”; what do you think?",
    ],
)
async def test_discord_ambiguous_role_mentions_do_not_dispatch_specialists(monkeypatch, text):
    from agent.biff_intent_router import route_biff_live_intent

    route = route_biff_live_intent(text, command=False)

    assert route.action not in {"forge_direct", "ranger_direct", "quill_direct", "vex_direct"}


@pytest.mark.asyncio
async def test_gateway_specialist_path_fails_closed_even_if_router_misclassifies(monkeypatch):
    from agent.biff_intent_router import BiffIntentRoute

    runner, handoff = _runner_with_mocked_handoff(monkeypatch)
    event = _discord_event("Vex should verify this before closeout.", "forced-vex-msg")
    monkeypatch.setattr(
        "agent.biff_intent_router.route_biff_live_intent",
        lambda *args, **kwargs: BiffIntentRoute("vex_direct", "forced test route", 2, False),
    )

    response = await runner._handle_message(event)

    assert response is not None
    assert "I did not send this to Vex" in response
    assert handoff.call_count == 0


@pytest.mark.asyncio
async def test_runtime_instability_guard_blocks_live_specialist_dispatch(monkeypatch):
    from gateway.session_hygiene import BiffRuntimeInstabilitySignal

    runner, handoff = _runner_with_mocked_handoff(monkeypatch)
    monkeypatch.setenv("HERMES_BIFF_RUNTIME_INSTABILITY_GUARD", "1")
    monkeypatch.setattr(
        "gateway.session_hygiene.inspect_biff_runtime_instability_logs",
        lambda *args, **kwargs: BiffRuntimeInstabilitySignal(
            True,
            ("recent_gateway_restarts", "codex_empty_terminal_frames"),
            sigterm_count=2,
            codex_empty_output_count=2,
        ),
    )
    event = _discord_event("Have Vex QA the dashboard change and verify the live UI actually works.", "unstable-vex-msg")

    response = await runner._handle_message(event)

    assert response is not None
    assert "I did not send this to Vex" in response
    assert "degraded live mode" in response
    assert handoff.call_count == 0


@pytest.mark.asyncio
async def test_discord_user_qa_message_hands_off_to_vex(monkeypatch):
    """Live-style guardrail: QA/validation work belongs to Vex."""

    runner, handoff = _runner_with_mocked_handoff(monkeypatch)
    event = _discord_event("Have Vex QA the dashboard change and verify the live UI actually works.", "user-msg-3")

    response = await runner._handle_message(event)

    assert response is not None
    assert "Vex" in response
    assert "Forge" not in response
    assert handoff.call_count == 1
    assert handoff.call_args.args[0] == "vex"


@pytest.mark.asyncio
async def test_discord_user_docs_research_message_hands_off_to_quill(monkeypatch):
    """Live-style guardrail: documentation/research work belongs to Quill."""

    runner, handoff = _runner_with_mocked_handoff(monkeypatch)
    event = _discord_event("Have Quill research and document the Biff routing contract in Obsidian.", "user-msg-4")

    response = await runner._handle_message(event)

    assert response is not None
    assert "Quill" in response
    assert "Forge" not in response
    assert handoff.call_count == 1
    assert handoff.call_args.args[0] == "quill"


def test_specialist_direct_toolsets_are_role_specific_and_not_starved():
    forge = set(_specialist_direct_toolsets("forge").split(","))
    ranger = set(_specialist_direct_toolsets("ranger").split(","))
    quill = set(_specialist_direct_toolsets("quill").split(","))
    vex = set(_specialist_direct_toolsets("vex").split(","))

    assert forge == {
        "terminal",
        "file",
        "web",
        "browser",
        "vision",
        "skills",
        "memory",
        "todo",
        "session_search",
        "code_execution",
        "kanban",
    }
    assert ranger == {"file", "skills", "memory", "todo", "session_search", "kanban"}
    assert quill == {
        "file",
        "web",
        "browser",
        "vision",
        "skills",
        "memory",
        "todo",
        "session_search",
        "kanban",
    }
    assert vex == {
        "terminal",
        "file",
        "web",
        "browser",
        "vision",
        "skills",
        "memory",
        "todo",
        "session_search",
        "code_execution",
        "kanban",
    }

    excluded_by_default = {"delegation", "cronjob", "messaging", "discord", "discord_admin", "homeassistant", "computer_use"}
    for role_toolsets in (forge, ranger, quill, vex):
        assert role_toolsets.isdisjoint(excluded_by_default)


@pytest.mark.asyncio
async def test_discord_specialist_handoff_creates_native_kanban_card_by_default(monkeypatch, tmp_path):
    """Biff-first specialist routing should enqueue native Kanban work, not ad hoc subprocess work."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BIFF_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.delenv("HERMES_BIFF_SPECIALISTS_USE_KANBAN", raising=False)
    from pathlib import Path as _Path
    monkeypatch.setattr(_Path, "home", lambda: tmp_path)

    runner = GatewayRunner(GatewayConfig())
    runner.adapters[Platform.DISCORD] = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(runner, "_is_user_authorized", lambda source: True)
    handoff = AsyncMock()
    monkeypatch.setattr(runner, "_run_specialist_direct_background_task", handoff)
    monkeypatch.setattr(
        "gateway.run._resolve_runtime_agent_kwargs",
        lambda: {"api_key": "test", "base_url": "https://example.invalid", "provider": "test"},
    )

    response = await runner._handle_message(
        _discord_event("Use Forge to fix the Kanban dispatcher bug and add tests.", "kanban-msg")
    )

    assert response is not None
    assert "Forge" in response
    assert "via Kanban" in response
    assert "/steer" in response
    assert handoff.await_count == 0

    from hermes_cli import kanban_db as kb
    with kb.connect(board="biff-os") as conn:
        tasks = kb.list_tasks(conn, assignee="forge", status="ready", tenant="biff-os")
        assert len(tasks) == 1
        task = tasks[0]
        assert task.workspace_kind == "dir"
    assert task.workspace_path == str(tmp_path / "runtime")
    assert task.created_by == "biff"
    assert task.display_id in response
    assert "Use Forge to fix the Kanban dispatcher bug" in (task.body or "")
    assert "Role handoff consent:" in (task.body or "")
    assert "source=current_message" in (task.body or "")
    assert "approval_phrase=Use Forge" in (task.body or "")
