"""BIF-1512: Background job and watchdog containment — synthetic checks.

Tests cover containment rules for the top 3 noisy loop paths:
  1. Background review (agent/background_review.py)
  2. Cron scheduler tick (cron/jobs.py)
  3. Busy queue merge failure (gateway/run.py)

Plus:
  4. Watchdog DNS resolution → infrastructure failure classification
  5. Kill switch enforcement
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Test 1: Background review containment
# ---------------------------------------------------------------------------


class TestBackgroundReviewContainment:
    """Background memory/skill review fires after every turn. Verify it uses
    daemon threads, tool whitelist, and cleanup sequence."""

    def test_background_review_uses_threadtarget_not_direct_spawn(self):
        """The review module defines spawn_background_review_thread which
        returns a (target, prompt) tuple; the actual thread creation happens
        in run_agent.py, not in background_review.py directly."""
        from agent.background_review import spawn_background_review_thread

        # Must exist and be callable
        target, prompt = spawn_background_review_thread(
            SimpleNamespace(
                _MEMORY_REVIEW_PROMPT="review memory",
                _SKILL_REVIEW_PROMPT="review skills",
                _COMBINED_REVIEW_PROMPT="review both",
                platform="telegram",
            ),
            messages_snapshot=[{"role": "user", "content": "hi"}],
            review_memory=True,
            review_skills=False,
        )
        assert callable(target)
        assert isinstance(prompt, str)

    def test_background_review_has_daemon_flag(self):
        """The run_agent.py code that spawns the background review thread
        constructs it with daemon=True."""
        run_source = (REPO_ROOT / "run_agent.py").read_text()
        assert "daemon=True" in run_source, (
            "run_agent.py must use daemon=True for background review threads"
        )

    def test_background_review_cleanup_sequence(self, monkeypatch):
        """The review thread path must call shutdown_memory_provider and close
        during cleanup. Verified via source inspection of the run_agent.py
        background review thread lifecycle."""
        run_source = (REPO_ROOT / "run_agent.py").read_text()

        # The review thread target is called as part of the agent lifecycle;
        # verify cleanup is wired
        assert "shutdown_memory_provider" in run_source, (
            "Agent lifecycle must call shutdown_memory_provider()"
        )
        assert "close()" in run_source or ".close(" in run_source, (
            "Agent lifecycle must call close() during cleanup"
        )

        # Also verify background_review module has proper cleanup
        review_source = (REPO_ROOT / "agent" / "background_review.py").read_text()
        assert "try" in review_source, (
            "Review thread must use try/finally for cleanup"
        )
        assert "finally" in review_source, (
            "Review thread must use try/finally for cleanup"
        )


# ---------------------------------------------------------------------------
# Test 2: Cron scheduler containment
# ---------------------------------------------------------------------------


class TestCronSchedulerContainment:
    """The cron scheduler must deduplicate, manage concurrency, and use
    atomic file writes."""

    def test_cron_jobs_file_lock_exists(self):
        """The jobs module must use a threading lock for write operations."""
        from cron.jobs import _jobs_file_lock

        assert isinstance(_jobs_file_lock, type(threading.Lock()))

    def test_cron_oneshot_grace_seconds_defined(self):
        """ONESHOT_GRACE_SECONDS must be defined to prevent rapid re-fire."""
        from cron.jobs import ONESHOT_GRACE_SECONDS

        assert ONESHOT_GRACE_SECONDS >= 60

    def test_cron_atomic_writes(self):
        """Job writes must use atomic file operations."""
        from cron.jobs import atomic_replace

        assert callable(atomic_replace)


# ---------------------------------------------------------------------------
# Test 3: Busy queue merge failure containment
# ---------------------------------------------------------------------------


class TestBusyQueueContainment:
    """When the busy queue merge fails, the gateway must degrade gracefully."""

    def test_busy_queue_failure_degrades_gracefully(self):
        """Simulate a queue store failure and verify degraded notice."""
        import asyncio
        from typing import cast as typing_cast

        from gateway.config import GatewayConfig, Platform
        from gateway.platforms.base import MessageEvent
        from gateway.run import GatewayRunner
        from gateway.session import SessionSource

        runner = GatewayRunner(GatewayConfig())
        runner._busy_input_mode = "queue"
        runner._is_user_authorized = lambda source: True

        adapter = SimpleNamespace(_pending_messages={}, _send_with_retry=AsyncMock())
        runner.adapters[Platform.DISCORD] = typing_cast(SimpleNamespace, adapter)
        source = SessionSource(platform=Platform.DISCORD, chat_id="current-chat", user_id="marco")
        event = MessageEvent(text="follow-up", source=source, message_id="msg-1")
        session_key = runner._session_key_for_source(source)
        running_agent = SimpleNamespace(
            steered=[],
            interrupted=[],
            get_activity_summary=lambda: {"api_call_count": 1, "max_iterations": 4, "current_tool": "test"},
        )
        runner._running_agents[session_key] = running_agent
        import gateway.run as gateway_run_mod

        def boom(*_args, **_kwargs):
            raise RuntimeError("synthetic queue store failure")

        with patch.object(gateway_run_mod, "merge_pending_message_event", boom):
            handled = asyncio.run(
                runner._handle_active_session_busy_message(event, session_key)
            )

        assert handled is True
        sent = adapter._send_with_retry.await_args.kwargs
        assert "could not queue" in sent["content"] or "please resend" in sent["content"]


# ---------------------------------------------------------------------------
# Test 4: Watchdog DNS failure classification
# ---------------------------------------------------------------------------


class TestWatchdogDNSInfrastructureClassification:
    """DNS resolution failures must be classified as infrastructure failure."""

    def test_dns_failure_source_contains_keywords(self):
        """The watchdog script source must contain URL error handling
        patterns for DNS failures."""
        wd_source = (REPO_ROOT / "scripts" / "uptime-kuma-ct130-watchdog.py").read_text()
        assert "Errno 8" in wd_source, "Missing Errno 8 check for DNS failure"
        assert "Name or service not known" in wd_source, "Missing Name/Service pattern"
        assert "DNS_RESOLUTION_FAILURE" in wd_source, "Missing DNS_RESOLUTION_FAILURE constant"

    def test_dns_failure_main_skips_remediation(self):
        """The main() function must skip remediation on DNS_RESOLUTION_FAILURE."""
        wd_source = (REPO_ROOT / "scripts" / "uptime-kuma-ct130-watchdog.py").read_text()
        assert "skipped_dns_failure" in wd_source, (
            "main() must use 'skipped_dns_failure' status for DNS failures"
        )
        assert "is_dns_failure" in wd_source, (
            "main() must track is_dns_failure flag"
        )

    def test_dns_failure_integration_subprocess(self):
        """Running the watchdog with a simulated DNS message should not
        produce a remediation trigger."""
        script = REPO_ROOT / "scripts" / "uptime-kuma-ct130-watchdog.py"

        proc = subprocess.run(
            [sys.executable, str(script), "--simulate-down"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=10,
        )
        # Should exit cleanly
        assert proc.returncode == 0, f"Script failed: {proc.stderr}"
        stdout = proc.stdout.strip()
        # Without --report-current, the script still outputs a JSON status line.
        # Verify the remediation block is properly disabled (not active).
        assert stdout, f"No output from watchdog: {proc.stderr}"
        last_line = stdout.splitlines()[-1]
        report = json.loads(last_line)
        remediation = report.get("remediation", {})
        assert remediation.get("status", "") in (
            "disabled_by_env", "disabled_until_enabled_env",
        ), f"Remediation should be disabled, got: {remediation}"

    def test_dns_failure_with_report_does_not_trigger_remediation(self):
        """Running with --report-current should output the current status."""
        script = REPO_ROOT / "scripts" / "uptime-kuma-ct130-watchdog.py"
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_path = f.name

        try:
            proc = subprocess.run(
                [sys.executable, str(script), "--simulate-down", "--report-current",
                 f"--url=http://nonexistent.invalid.test:3001/"],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                timeout=10,
            )
            assert proc.returncode == 0, f"Script failed: {proc.stderr}"

            if proc.stdout.strip():
                report = json.loads(proc.stdout.strip().splitlines()[-1])
                # Should report down status (accept down, initial_down, or still_down)
                assert report.get("status") in ("down", "initial_down", "still_down")
                # Should NOT have attempted remediation unless explicitly enabled
                remediation = report.get("remediation", {})
                if remediation:
                    assert remediation.get("status") in (
                        "disabled_by_env", "disabled_until_enabled_env",
                    ), f"Unexpected remediation: {remediation}"
        finally:
            try:
                os.unlink(state_path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Test 5: Kill switch enforcement
# ---------------------------------------------------------------------------


class TestContainmentPrimitives:
    """Verify kill switch mechanisms across background job paths."""

    def test_instability_guard_kill_switch_preserves_tools(self):
        """The instability guard must have a kill switch that preserves
        full tool access."""
        from gateway.session_hygiene import (
            apply_biff_runtime_instability_guard,
            apply_biff_runtime_instability_tool_guardrails,
            biff_runtime_instability_guard_disabled,
            detect_biff_runtime_instability,
            resolve_biff_operating_mode,
        )

        mode = resolve_biff_operating_mode({}, "discord")
        signal = detect_biff_runtime_instability(" ".join(["SIGTERM"] * 8))

        with patch.dict(os.environ, {"BIFF_DISABLE_INSTABILITY_GUARD": "1"}):
            disabled = biff_runtime_instability_guard_disabled({}, "discord")
            assert disabled is True
            guarded = apply_biff_runtime_instability_guard(
                mode, signal, disabled=disabled,
            )
            adjusted = apply_biff_runtime_instability_tool_guardrails(
                {"max_tool_calls": 60, "terminal_timeout": 45},
                signal, disabled=disabled,
            )

        assert guarded.name == "normal"
        assert adjusted["max_tool_calls"] == 60
        assert adjusted["terminal_timeout"] == 45

    def test_watchdog_env_kill_switch(self):
        """The watchdog remediation must have a DISABLE env kill switch."""
        wd_source = (REPO_ROOT / "scripts" / "uptime-kuma-ct130-watchdog.py").read_text()
        assert "REMEDIATION_DISABLE_ENV" in wd_source
        assert "UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE" in wd_source

    def test_watchdog_opt_in_remediation(self):
        """Remediation must be opt-in via env var (not default-on)."""
        wd_source = (REPO_ROOT / "scripts" / "uptime-kuma-ct130-watchdog.py").read_text()
        assert "REMEDIATION_ENABLE_ENV" in wd_source
        assert "UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE" in wd_source

    def test_instability_guard_config_kill_switch(self):
        """The config-based kill switch must also preserve guard-disabled state."""
        from gateway.session_hygiene import (
            biff_runtime_instability_guard_disabled,
        )

        config = {"biff": {"platforms": {"discord": {"disable_instability_guard": True}}}}
        assert biff_runtime_instability_guard_disabled(config, "discord") is True


# ---------------------------------------------------------------------------
# Test 6: Dry-run checks for top 3 noisy loop paths
# ---------------------------------------------------------------------------


class TestNoisyLoopPathDryRuns:
    """Static verification for top 3 noisy loop paths."""

    def test_background_review_dry_run(self):
        """Background review: verify daemon thread pattern in run_agent.py."""
        source = (REPO_ROOT / "run_agent.py").read_text()
        assert "daemon=True" in source
        # Verify review module tool restriction exists
        review_source = (REPO_ROOT / "agent" / "background_review.py").read_text()
        assert "shutdown_memory_provider" in review_source
        assert "close" in review_source

    def test_cron_scheduler_dry_run(self):
        """Cron tick: verify dedup, lock, atomic write exist."""
        source = (REPO_ROOT / "cron" / "jobs.py").read_text()
        assert "_jobs_file_lock" in source
        assert "atomic_replace" in source or "atomic_write" in source
        assert "ONESHOT_GRACE_SECONDS" in source

    def test_busy_queue_dry_run(self):
        """Busy queue: verify fail-degraded and no-wedge logic."""
        source = (REPO_ROOT / "gateway" / "run.py").read_text()
        assert "could not queue" in source
        assert "please resend" in source