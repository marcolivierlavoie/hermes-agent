import asyncio
import shutil
import subprocess
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from agent.i18n import t
from gateway.platforms.base import MessageEvent, MessageType
from gateway.restart import DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
from gateway.session import SessionEntry, build_session_key
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


def test_macos_orphan_gateway_uses_detached_restart_not_service(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(gateway_run.sys, "platform", "darwin")
    monkeypatch.setattr(gateway_run.os, "getppid", lambda: 1)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 113, stdout="", stderr="not loaded")

    monkeypatch.setattr(gateway_run.subprocess, "run", fake_run)

    assert gateway_run._running_under_service_manager() is False


def test_macos_launchd_gateway_uses_service_restart(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(gateway_run.sys, "platform", "darwin")
    monkeypatch.setattr(gateway_run.os, "getppid", lambda: 1)
    monkeypatch.setattr(gateway_run.os, "getpid", lambda: 1234)
    monkeypatch.setattr(gateway_run.os, "getuid", lambda: 501)
    monkeypatch.setattr(gateway_run, "_gateway_launchd_label", lambda: "ai.hermes.gateway-biff")
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, stdout='\tpid = 1234\n', stderr="")

    monkeypatch.setattr(gateway_run.subprocess, "run", fake_run)

    assert gateway_run._running_under_service_manager() is True
    assert calls == [["launchctl", "print", "system/ai.hermes.gateway-biff"]]


def test_macos_launchagent_gateway_uses_service_restart_after_system_miss(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(gateway_run.sys, "platform", "darwin")
    monkeypatch.setattr(gateway_run.os, "getppid", lambda: 1)
    monkeypatch.setattr(gateway_run.os, "getpid", lambda: 1234)
    monkeypatch.setattr(gateway_run.os, "getuid", lambda: 501)
    monkeypatch.setattr(gateway_run, "_gateway_launchd_label", lambda: "ai.hermes.gateway-biff")
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        if args[0][-1].startswith("system/"):
            return subprocess.CompletedProcess(args[0], 113, stdout="", stderr="not loaded")
        return subprocess.CompletedProcess(args[0], 0, stdout='\tpid = 1234\n', stderr="")

    monkeypatch.setattr(gateway_run.subprocess, "run", fake_run)

    assert gateway_run._running_under_service_manager() is True
    assert calls == [
        ["launchctl", "print", "system/ai.hermes.gateway-biff"],
        ["launchctl", "print", "gui/501/ai.hermes.gateway-biff"],
    ]


def test_macos_launchd_pid_mismatch_uses_detached_restart(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(gateway_run.sys, "platform", "darwin")
    monkeypatch.setattr(gateway_run.os, "getppid", lambda: 1)
    monkeypatch.setattr(gateway_run.os, "getpid", lambda: 1234)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout='\tpid = 9999\n', stderr="")

    monkeypatch.setattr(gateway_run.subprocess, "run", fake_run)

    assert gateway_run._running_under_service_manager() is False


@pytest.mark.asyncio
async def test_restart_command_while_busy_requests_drain_without_interrupt(monkeypatch):
    # Ensure INVOCATION_ID is NOT set — systemd sets this in service mode,
    # which changes the restart call signature.
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(gateway_run.sys, "platform", "linux")
    monkeypatch.setattr(gateway_run.os, "getppid", lambda: 4242)
    monkeypatch.setattr(gateway_run, "_running_in_container", lambda: False)
    runner, _adapter = make_restart_runner()
    runner.request_restart = MagicMock(return_value=True)
    event = MessageEvent(
        text="/restart",
        message_type=MessageType.TEXT,
        source=make_restart_source(),
        message_id="m1",
    )
    session_key = build_session_key(event.source)
    running_agent = MagicMock()
    runner._running_agents[session_key] = running_agent

    result = await runner._handle_message(event)

    expected = t("gateway.draining", count=1)
    assert result == expected
    # Guard against the silent-degradation regression in #22266: if the i18n
    # catalog cannot be resolved (e.g. xdist workers losing the locales path)
    # then ``t("gateway.draining", count=1)`` returns the bare key
    # ``"gateway.draining"`` instead of the formatted English string, and both
    # sides of the equality above would still match. Assert on the catalog
    # output explicitly so a broken locale resolution fails loudly here.
    assert expected != "gateway.draining"
    assert "Draining" in expected and "1" in expected
    running_agent.interrupt.assert_not_called()
    runner.request_restart.assert_called_once_with(detached=True, via_service=False)


@pytest.mark.asyncio
async def test_drain_queue_mode_queues_follow_up_without_interrupt():
    runner, adapter = make_restart_runner()
    runner._draining = True
    runner._restart_requested = True
    runner._busy_input_mode = "queue"

    event = MessageEvent(
        text="follow up",
        message_type=MessageType.TEXT,
        source=make_restart_source(),
        message_id="m2",
    )
    session_key = build_session_key(event.source)
    adapter._active_sessions[session_key] = asyncio.Event()

    await adapter.handle_message(event)

    assert session_key in adapter._pending_messages
    assert adapter._pending_messages[session_key].text == "follow up"
    assert not adapter._active_sessions[session_key].is_set()
    assert any("queued for the next turn" in message for message in adapter.sent)


@pytest.mark.asyncio
async def test_draining_rejects_new_session_messages():
    runner, _adapter = make_restart_runner()
    runner._draining = True
    runner._restart_requested = True

    event = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=make_restart_source("fresh"),
        message_id="m3",
    )

    result = await runner._handle_message(event)

    assert result == "⏳ Gateway is restarting and is not accepting new work right now."


def test_load_busy_input_mode_prefers_env_then_config_then_default(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.delenv("HERMES_GATEWAY_BUSY_INPUT_MODE", raising=False)

    assert gateway_run.GatewayRunner._load_busy_input_mode() == "interrupt"

    (tmp_path / "config.yaml").write_text(
        "display:\n  busy_input_mode: queue\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_busy_input_mode() == "queue"

    (tmp_path / "config.yaml").write_text(
        "display:\n  busy_input_mode: steer\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_busy_input_mode() == "steer"

    monkeypatch.setenv("HERMES_GATEWAY_BUSY_INPUT_MODE", "interrupt")
    assert gateway_run.GatewayRunner._load_busy_input_mode() == "interrupt"

    monkeypatch.setenv("HERMES_GATEWAY_BUSY_INPUT_MODE", "steer")
    assert gateway_run.GatewayRunner._load_busy_input_mode() == "steer"

    # Unknown values fall through to the safe default
    monkeypatch.setenv("HERMES_GATEWAY_BUSY_INPUT_MODE", "bogus")
    assert gateway_run.GatewayRunner._load_busy_input_mode() == "interrupt"


def test_load_busy_text_mode_defaults_to_queue_and_allows_interrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.delenv("HERMES_GATEWAY_BUSY_TEXT_MODE", raising=False)

    assert gateway_run.GatewayRunner._load_busy_text_mode() == "queue"

    (tmp_path / "config.yaml").write_text(
        "display:\n  busy_text_mode: interrupt\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_busy_text_mode() == "interrupt"

    monkeypatch.setenv("HERMES_GATEWAY_BUSY_TEXT_MODE", "queue")
    assert gateway_run.GatewayRunner._load_busy_text_mode() == "queue"

    monkeypatch.setenv("HERMES_GATEWAY_BUSY_TEXT_MODE", "bogus")
    assert gateway_run.GatewayRunner._load_busy_text_mode() == "queue"


def test_load_restart_drain_timeout_prefers_env_then_config_then_default(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.delenv("HERMES_RESTART_DRAIN_TIMEOUT", raising=False)

    assert (
        gateway_run.GatewayRunner._load_restart_drain_timeout()
        == DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    )

    (tmp_path / "config.yaml").write_text(
        "agent:\n  restart_drain_timeout: 12\n", encoding="utf-8"
    )
    assert gateway_run.GatewayRunner._load_restart_drain_timeout() == 12.0

    monkeypatch.setenv("HERMES_RESTART_DRAIN_TIMEOUT", "7")
    assert gateway_run.GatewayRunner._load_restart_drain_timeout() == 7.0

    monkeypatch.setenv("HERMES_RESTART_DRAIN_TIMEOUT", "invalid")
    assert (
        gateway_run.GatewayRunner._load_restart_drain_timeout()
        == DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    )
    assert "Invalid restart_drain_timeout" in caplog.text


@pytest.mark.asyncio
async def test_request_restart_is_idempotent():
    runner, _adapter = make_restart_runner()
    runner.stop = AsyncMock()

    assert runner.request_restart(detached=True, via_service=False) is True
    first_task = next(iter(runner._background_tasks))
    assert runner.request_restart(detached=True, via_service=False) is False

    await first_task

    runner.stop.assert_awaited_once_with(
        restart=True, detached_restart=True, service_restart=False
    )


@pytest.mark.asyncio
async def test_request_restart_driver_cancellation_does_not_cancel_stop(monkeypatch):
    runner, _adapter = make_restart_runner()
    stop_started = asyncio.Event()
    stop_may_finish = asyncio.Event()
    stop_completed = asyncio.Event()

    async def fake_stop(**_kwargs):
        stop_started.set()
        await stop_may_finish.wait()
        stop_completed.set()

    runner.stop = fake_stop

    assert runner.request_restart(detached=True, via_service=False) is True
    restart_task = next(iter(runner._background_tasks))

    await asyncio.wait_for(stop_started.wait(), timeout=1)
    restart_task.cancel()
    await asyncio.sleep(0)

    assert stop_completed.is_set() is False
    stop_may_finish.set()
    await asyncio.wait_for(stop_completed.wait(), timeout=1)
    with pytest.raises(asyncio.CancelledError):
        await restart_task


@pytest.mark.asyncio
async def test_restart_stop_cancels_restart_driver_without_aborting_teardown(monkeypatch, tmp_path):
    runner, _adapter = make_restart_runner()
    runner.stop = gateway_run.GatewayRunner.stop.__get__(runner, gateway_run.GatewayRunner)
    runner._drain_active_agents = AsyncMock(return_value=({}, False))
    runner._finalize_shutdown_agents = MagicMock()
    runner._launch_detached_restart_command = AsyncMock()
    runner._cleanup_agent_resources = MagicMock()
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    assert runner.request_restart(detached=False, via_service=True) is True

    await asyncio.wait_for(runner._shutdown_event.wait(), timeout=2)
    assert runner._draining is False
    assert runner._exit_code == gateway_run.GATEWAY_SERVICE_RESTART_EXIT_CODE
    assert runner._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_launch_detached_restart_command_uses_bounded_python_watcher(monkeypatch, tmp_path):
    runner, _adapter = make_restart_runner()
    popen_calls = []

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_hermes_bin", lambda: ["/usr/bin/hermes"])
    monkeypatch.setattr(gateway_run.os, "getpid", lambda: 321)
    monkeypatch.setenv("HERMES_DETACHED_RESTART_MAX_WAIT", "3")
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/setsid" if cmd == "setsid" else None)

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return MagicMock()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    await runner._launch_detached_restart_command()

    assert len(popen_calls) == 1
    cmd, kwargs = popen_calls[0]
    assert cmd[0] == "/usr/bin/setsid"
    assert cmd[1:4] == [gateway_run.sys.executable, "-c", cmd[3]]
    assert "max wait exceeded" in cmd[3]
    assert "ready marker detected" in cmd[3]
    assert cmd[4:8] == ["321", "3.0", str(tmp_path / ".restart_ready.321"), str(tmp_path / "logs" / "gateway-restart-watcher.log")]
    assert cmd[-3:] == ["/usr/bin/hermes", "gateway", "restart"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_detached_restart_ready_marker_and_force_exit(monkeypatch, tmp_path):
    runner, _adapter = make_restart_runner()
    exits = []

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run.os, "getpid", lambda: 654)
    monkeypatch.setattr(gateway_run.os, "_exit", lambda code: exits.append(code))

    runner._mark_detached_restart_ready()
    assert (tmp_path / ".restart_ready.654").exists()

    runner._force_exit_after_clean_detached_restart()
    assert exits == [0]


def test_load_detached_restart_max_wait_prefers_env(monkeypatch):
    monkeypatch.setenv("HERMES_DETACHED_RESTART_MAX_WAIT", "4.5")
    assert gateway_run.GatewayRunner._load_detached_restart_max_wait() == 4.5

    monkeypatch.setenv("HERMES_DETACHED_RESTART_MAX_WAIT", "invalid")
    assert (
        gateway_run.GatewayRunner._load_detached_restart_max_wait()
        == gateway_run.DEFAULT_GATEWAY_DETACHED_RESTART_MAX_WAIT
    )


# ── Shutdown notification tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_notification_sent_to_active_sessions():
    """Active sessions receive a notification when the gateway starts shutting down."""
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="999", chat_type="dm")
    session_key = f"agent:main:telegram:dm:999"
    runner._running_agents[session_key] = MagicMock()

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 1
    assert "shutting down" in adapter.sent[0]
    assert "interrupted" in adapter.sent[0]


@pytest.mark.asyncio
async def test_shutdown_notification_says_restarting_when_restart_requested():
    """When _restart_requested is True, the message says 'restarting' and mentions /retry."""
    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    session_key = "agent:main:telegram:dm:999"
    runner._running_agents[session_key] = MagicMock()

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 1
    assert "restarting" in adapter.sent[0]
    assert "resume" in adapter.sent[0]


@pytest.mark.asyncio
async def test_shutdown_notification_deduplicates_per_chat():
    """Multiple sessions in the same chat only get one notification."""
    runner, adapter = make_restart_runner()
    # Two sessions (different users) in the same chat
    runner._running_agents["agent:main:telegram:group:chat1:u1"] = MagicMock()
    runner._running_agents["agent:main:telegram:group:chat1:u2"] = MagicMock()

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 1


@pytest.mark.asyncio
async def test_shutdown_notification_skipped_when_no_active_agents():
    """No notification is sent when there are no active agents."""
    runner, adapter = make_restart_runner()

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 0


@pytest.mark.asyncio
async def test_shutdown_notification_ignores_pending_sentinels():
    """Pending sentinels (not-yet-started agents) don't trigger notifications."""
    from gateway.run import _AGENT_PENDING_SENTINEL

    runner, adapter = make_restart_runner()
    runner._running_agents["agent:main:telegram:dm:999"] = _AGENT_PENDING_SENTINEL

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 0


@pytest.mark.asyncio
async def test_shutdown_notification_send_failure_does_not_block():
    """If sending a notification fails, the method still completes."""
    runner, adapter = make_restart_runner()
    adapter.send = AsyncMock(side_effect=Exception("network error"))
    session_key = "agent:main:telegram:dm:999"
    runner._running_agents[session_key] = MagicMock()

    # Should not raise
    await runner._notify_active_sessions_of_shutdown()


@pytest.mark.asyncio
async def test_shutdown_notification_suppressed_when_flag_disabled():
    """Active-session ping is muted when gateway_restart_notification=False on the platform."""
    from gateway.config import Platform

    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    runner.config.platforms[Platform.TELEGRAM].gateway_restart_notification = False
    session_key = "agent:main:telegram:dm:999"
    runner._running_agents[session_key] = MagicMock()

    await runner._notify_active_sessions_of_shutdown()

    assert adapter.sent == []


@pytest.mark.asyncio
async def test_shutdown_notification_home_channel_suppressed_when_flag_disabled():
    """Home-channel ping during shutdown is muted when the flag is False."""
    from gateway.config import HomeChannel, Platform

    runner, adapter = make_restart_runner()
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="home-42",
        name="Ops Home",
    )
    runner.config.platforms[Platform.TELEGRAM].gateway_restart_notification = False

    await runner._notify_active_sessions_of_shutdown()

    assert adapter.sent == []


@pytest.mark.asyncio
async def test_shutdown_notification_uses_persisted_origin_for_colon_ids():
    """Shutdown notifications should route from persisted origin, not reparsed keys."""
    runner, adapter = make_restart_runner()
    adapter.send = AsyncMock()
    source = make_restart_source(chat_id="!room123:example.org", chat_type="group")
    source.platform = gateway_run.Platform.MATRIX
    session_key = build_session_key(source)
    runner._running_agents[session_key] = MagicMock()
    runner.session_store._entries = {
        session_key: SessionEntry(
            session_key=session_key,
            session_id="sess-1",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=source.platform,
            chat_type=source.chat_type,
        )
    }
    runner.adapters = {gateway_run.Platform.MATRIX: adapter}

    await runner._notify_active_sessions_of_shutdown()

    assert adapter.send.await_count == 1
    assert adapter.send.await_args.args[0] == "!room123:example.org"
