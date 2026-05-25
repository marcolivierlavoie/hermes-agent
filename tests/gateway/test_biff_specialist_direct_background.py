import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource


RUN_PY = Path(__file__).resolve().parents[2] / "gateway" / "run.py"


def test_specialist_direct_dispatch_ack_keeps_hermes_free():
    source = RUN_PY.read_text()

    assert "I’m sending this to {_specialist_role.title()} now" in source
    assert "I’ll keep chatting here and post progress in #biff-ops" in source
    assert "I asked {_specialist_role.title()} to take this in the background" not in source
    assert "It will keep #biff-ops updated with useful summaries and leave full detail in #{_specialist_role}" not in source
    assert "I’ll bring the final done/blocked result back here" not in source
    assert "#hermes stays open for new questions and decisions" not in source
    assert "will report back here with done/blocked evidence" not in source
    assert "will report back here; I won’t start a duplicate copy" not in source


def test_specialist_direct_duplicate_ack_keeps_hermes_free():
    source = RUN_PY.read_text()

    duplicate_block = source[source.index("biff_nonblocking_specialist_deduped"):source.index("_task_id = f", source.index("biff_nonblocking_specialist_deduped"))]
    assert "is already handling that" in duplicate_block
    assert "I’ll keep chatting here and post progress in #biff-ops" in duplicate_block
    assert "#biff-ops has the operational summary" not in duplicate_block
    assert "I’ll bring the final done/blocked result back here" not in duplicate_block
    assert "#hermes stays open" not in duplicate_block
    assert "will report back here" not in duplicate_block


def test_specialist_direct_invocation_gets_task_id_for_ops_feed():
    source = RUN_PY.read_text()

    assert (
        '"--toolsets",\n'
        "                specialist_toolsets,\n"
        '                "--issue",\n'
        "                task_id,\n"
        '                "--timeout"'
    ) in source


def test_specialist_direct_progress_does_not_post_to_command_room_but_completion_does():
    source = RUN_PY.read_text()
    marker = "biff_specialist_direct_background_done"
    progress_block = source[
        source.index("while not forge_task.done():"):source.index("returncode = int(getattr(result", source.index("while not forge_task.done():"))
    ]
    completion_block = source[source.index("role_status = "):source.index("        except Exception as e:", source.index("role_status = "))]

    assert "await adapter.send" not in progress_block
    assert "close the loop with Marco" in completion_block
    assert "raw worker stdout/stderr out" in completion_block
    assert "await adapter.send" in completion_block
    assert 'f"{role.title()} background task `{task_id}` {role_status}{detail_suffix}' in completion_block
    assert 'role_status = "done" if returncode == 0 else "blocked"' in completion_block
    assert "body =" not in completion_block
    assert "stdout or stderr" not in completion_block
    assert marker in completion_block


@pytest.mark.asyncio
async def test_specialist_direct_timeout_progress_does_not_send_to_hermes_but_completion_relays(monkeypatch):
    runner = GatewayRunner(GatewayConfig())
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters[Platform.DISCORD] = cast(Any, adapter)
    source = SessionSource(platform=Platform.DISCORD, chat_id="hermes-channel", user_id="marco")

    original_sleep = asyncio.sleep

    async def fake_executor(_run_sync):
        await original_sleep(0)
        return subprocess.CompletedProcess(
            args=["biff_role_invoke.py"],
            returncode=0,
            stdout='{"stdout": "verified evidence from Forge"}',
            stderr="",
        )

    wait_for_calls = 0

    async def fake_wait_for(awaitable, timeout):
        nonlocal wait_for_calls
        wait_for_calls += 1
        if wait_for_calls == 1:
            raise asyncio.TimeoutError
        return await awaitable

    monkeypatch.setattr(runner, "_run_in_executor_with_context", fake_executor)
    monkeypatch.setattr(gateway_run.asyncio, "wait_for", fake_wait_for)

    await runner._run_specialist_direct_background_task("forge", "do work", source, "forge_123")

    assert adapter.send.await_count == 1
    chat_id, message = adapter.send.await_args.args[:2]
    assert chat_id == "hermes-channel"
    assert "Forge background task `forge_123` done" in message
    assert "elapsed" not in message
    assert "verified evidence from Forge" not in message
    assert "Lifecycle: #biff-ops" in message
    assert "Full worker detail: #forge" in message
    assert "#hermes stays free" in message
    assert wait_for_calls >= 1


@pytest.mark.asyncio
async def test_specialist_direct_missing_invocation_script_relays_blocked_outcome(monkeypatch):
    runner = GatewayRunner(GatewayConfig())
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters[Platform.DISCORD] = cast(Any, adapter)
    source = SessionSource(platform=Platform.DISCORD, chat_id="hermes-channel", user_id="marco")

    monkeypatch.setattr(gateway_run.Path, "exists", lambda _self: False)

    await runner._run_specialist_direct_background_task("forge", "do work", source, "forge_missing")

    assert adapter.send.await_count == 1
    chat_id, message = adapter.send.await_args.args[:2]
    assert chat_id == "hermes-channel"
    assert "Forge background task `forge_missing` blocked before specialist launch" in message
    assert "failed" not in message
    assert "Lifecycle: #biff-ops" in message
    assert "Full worker detail: #forge" in message


@pytest.mark.asyncio
async def test_specialist_direct_internal_exception_relays_blocked_outcome(monkeypatch):
    runner = GatewayRunner(GatewayConfig())
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters[Platform.DISCORD] = cast(Any, adapter)
    source = SessionSource(platform=Platform.DISCORD, chat_id="hermes-channel", user_id="marco")

    async def fake_executor(_run_sync):
        raise RuntimeError("synthetic worker crash")

    monkeypatch.setattr(runner, "_run_in_executor_with_context", fake_executor)

    await runner._run_specialist_direct_background_task("forge", "do work", source, "forge_456")

    assert adapter.send.await_count == 1
    chat_id, message = adapter.send.await_args.args[:2]
    assert chat_id == "hermes-channel"
    assert "Forge background task `forge_456` blocked before final relay" in message
    assert "synthetic worker crash" not in message
    assert "Check gateway logs for the internal exception" in message
