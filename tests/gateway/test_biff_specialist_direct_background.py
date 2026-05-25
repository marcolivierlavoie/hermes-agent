import asyncio
import sqlite3
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
    assert "restore the high-signal completion recap" in completion_block
    assert "#hermes gets the role's actual conclusion plus a next" in completion_block
    assert "await adapter.send" in completion_block
    assert "_format_specialist_direct_completion_recap" in completion_block
    assert 'role_status = "done" if returncode == 0 else "blocked"' in completion_block
    assert "body =" not in completion_block
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
    assert "**Status:** Done" in message
    assert "**Result / what changed:**" in message
    assert "verified evidence from Forge" in message
    assert "**Evidence:**" in message
    assert "Lifecycle: #biff-ops" in message
    assert "raw detail: #forge" in message
    assert "**Next step:**" in message
    assert "No action needed from Marco" in message
    assert wait_for_calls >= 1


@pytest.mark.asyncio
async def test_specialist_direct_completion_falls_back_to_non_json_stdout(monkeypatch):
    runner = GatewayRunner(GatewayConfig())
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters[Platform.DISCORD] = cast(Any, adapter)
    source = SessionSource(platform=Platform.DISCORD, chat_id="hermes-channel", user_id="marco")

    async def fake_executor(_run_sync):
        return subprocess.CompletedProcess(
            args=["biff_role_invoke.py"],
            returncode=0,
            stdout="plain-text role recap",
            stderr="",
        )

    monkeypatch.setattr(runner, "_run_in_executor_with_context", fake_executor)

    await runner._run_specialist_direct_background_task("forge", "do work", source, "forge_plain")

    assert adapter.send.await_count == 1
    _chat_id, message = adapter.send.await_args.args[:2]
    assert "Forge background task `forge_plain` done" in message
    assert "plain-text role recap" in message
    assert "Completed, but no written role summary was recoverable" not in message


@pytest.mark.asyncio
async def test_specialist_direct_completion_falls_back_to_role_session_when_stdout_empty(monkeypatch, tmp_path):
    runner = GatewayRunner(GatewayConfig())
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters[Platform.DISCORD] = cast(Any, adapter)
    source = SessionSource(platform=Platform.DISCORD, chat_id="hermes-channel", user_id="marco")

    session_id = "20260525_113429_43b469"
    session_dir = tmp_path / "profiles" / "forge" / "sessions"
    session_dir.mkdir(parents=True)
    (session_dir / f"session_{session_id}.json").write_text(
        '{"messages": ['
        '{"role": "assistant", "content": ""},'
        '{"role": "tool", "content": "{}"},'
        '{"role": "assistant", "content": "actual final Forge summary"}'
        ']}'
    )
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    async def fake_executor(_run_sync):
        return subprocess.CompletedProcess(
            args=["biff_role_invoke.py"],
            returncode=0,
            stdout=(
                '{"stdout": "", "session_id": "20260525_113429_43b469", '
                '"elapsed_seconds": 232.3}'
            ),
            stderr="",
        )

    monkeypatch.setattr(runner, "_run_in_executor_with_context", fake_executor)

    await runner._run_specialist_direct_background_task("forge", "do work", source, "forge_empty")

    assert adapter.send.await_count == 1
    _chat_id, message = adapter.send.await_args.args[:2]
    assert "Forge background task `forge_empty` done" in message
    assert "session `20260525_113429_43b469`" in message
    assert "actual final Forge summary" in message
    assert "Completed, but no written role summary was recoverable" not in message


@pytest.mark.asyncio
async def test_specialist_direct_completion_falls_back_to_role_state_db_when_json_absent(monkeypatch, tmp_path):
    runner = GatewayRunner(GatewayConfig())
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters[Platform.DISCORD] = cast(Any, adapter)
    source = SessionSource(platform=Platform.DISCORD, chat_id="hermes-channel", user_id="marco")

    session_id = "20260525_115426_1e84e3"
    db_dir = tmp_path / "profiles" / "vex"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "state.db"
    with sqlite3.connect(str(db_path)) as con:
        con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp INTEGER)")
        con.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (session_id, "assistant", "PASS\n\nRecovered DB-only Vex summary", 1),
        )
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    async def fake_executor(_run_sync):
        return subprocess.CompletedProcess(
            args=["biff_role_invoke.py"],
            returncode=0,
            stdout=(
                '{"stdout": "", "session_id": "20260525_115426_1e84e3", '
                '"elapsed_seconds": 111.1}'
            ),
            stderr="",
        )

    monkeypatch.setattr(runner, "_run_in_executor_with_context", fake_executor)

    await runner._run_specialist_direct_background_task("vex", "verify work", source, "vex_db_only")

    assert adapter.send.await_count == 1
    _chat_id, message = adapter.send.await_args.args[:2]
    assert "Vex background task `vex_db_only` done" in message
    assert "session `20260525_115426_1e84e3`" in message
    assert "Recovered DB-only Vex summary" in message
    assert "Completed, but no written role summary was recoverable" not in message


def test_specialist_direct_completion_recap_uses_executive_closeout_shape():
    message = GatewayRunner._format_specialist_direct_completion_recap(
        role="forge",
        task_id="forge_done",
        role_status="done",
        detail_suffix=" (elapsed 12.3s, session `abc`)",
        role_output="What changed:\n- Fixed the route.\n\nEvidence:\n- Tests passed.",
    )

    assert "Forge background task `forge_done` done" in message
    assert "**Status:** Done" in message
    assert "**Result / what changed:**" in message
    assert "Fixed the route" in message
    assert "**Evidence:** Lifecycle: #biff-ops, raw detail: #forge, elapsed 12.3s, session `abc`." in message
    assert "**Next step:** No action needed from Marco" in message
    assert "**Recap from Forge:**" not in message


def test_specialist_direct_blocked_recap_uses_decision_shape():
    message = GatewayRunner._format_specialist_direct_completion_recap(
        role="vex",
        task_id="vex_blocked",
        role_status="blocked",
        detail_suffix=" (exit 2, session `def`)",
        role_output="",
        stderr_tail="Vex decision was BLOCKED",
    )

    assert "Vex background task `vex_blocked` blocked" in message
    assert "**Status:** Blocked" in message
    assert "**Blocker / decision needed:**" in message
    assert "Vex decision was BLOCKED" in message
    assert "**Evidence:** Lifecycle: #biff-ops, raw detail: #vex, exit 2, session `def`." in message
    assert "**Next step:** Biff should change strategy" in message


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
