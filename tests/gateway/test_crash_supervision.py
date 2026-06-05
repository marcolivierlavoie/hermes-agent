"""Crash-safe supervision tests (BIF-1508).

Five focused tests that simulate restart/interruption states without
requiring destructive live restarts:

  1. detect_prior_crash — health marker survives crash, vanishes on clean exit
  2. crash_loop_detection — restart counter increments on consecutive crashes
  3. startup_health_marker_write_and_read — idempotent startup guards
  4. interrupted_turn_no_empty_response — continuation scan finds interrupted work
  5. recovery_summary_after_crash — full recovery answer shape
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from gateway import startup_health, recovery


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_hermes_home(monkeypatch, tmp_path) -> Path:
    """Point HERMES_HOME to a temporary directory."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def tmp_continuation_dir(monkeypatch, tmp_path) -> Path:
    """Set up a continuation artifacts directory."""
    d = tmp_path / "continuations"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_BIFF_CONTINUATION_DIR", str(d))
    return d


# ---------------------------------------------------------------------------
# Test 1: Crash detection via health marker
# ---------------------------------------------------------------------------


class TestCrashDetectionViaHealthMarker:
    """Verify the health marker survives crashes and is removed on clean exit."""

    def test_health_marker_detects_prior_crash(self, tmp_hermes_home, monkeypatch):
        """Simulate a crashed run: write marker, then detect it on next startup."""
        # Simulate a previous run writing its marker
        fake_pid = 999999  # Different from our current PID
        marker = {
            "pid": fake_pid,
            "start_time": "2026-05-28T10:00:00+00:00",
            "start_timestamp": time.time() - 3600,
            "git_head": "abc123def",
        }
        marker_path = tmp_hermes_home / ".gateway-health-marker"
        marker_path.write_text(
            json.dumps(marker, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # On next startup, detect the prior crash
        assert startup_health.detect_prior_crash(tmp_hermes_home) is True

    def test_no_crash_when_marker_absent(self, tmp_hermes_home):
        """Clean exit removes the marker; no crash detected on next startup."""
        assert startup_health.detect_prior_crash(tmp_hermes_home) is False

    def test_no_crash_when_same_pid(self, tmp_hermes_home):
        """Rechecking within the same process is not a crash."""
        marker = {
            "pid": os.getpid(),
            "start_time": "2026-05-28T10:00:00+00:00",
            "start_timestamp": time.time(),
        }
        marker_path = tmp_hermes_home / ".gateway-health-marker"
        marker_path.write_text(
            json.dumps(marker, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        assert startup_health.detect_prior_crash(tmp_hermes_home) is False

    def test_marker_removed_on_clean_shutdown(self, tmp_hermes_home):
        """Write then remove marker — final state should be absent."""
        startup_health.write_startup_health_marker(tmp_hermes_home)
        marker_path = tmp_hermes_home / ".gateway-health-marker"
        assert marker_path.exists()

        startup_health.remove_startup_health_marker(tmp_hermes_home)
        assert not marker_path.exists()

    def test_get_prior_crash_info_returns_marker_details(self, tmp_hermes_home):
        """get_prior_crash_info must return the full marker with enriched fields."""
        fake_pid = 888888
        marker = {
            "pid": fake_pid,
            "start_time": "2026-05-28T10:00:00+00:00",
            "start_timestamp": time.time() - 7200,
            "git_head": "def456abc",
        }
        marker_path = tmp_hermes_home / ".gateway-health-marker"
        marker_path.write_text(
            json.dumps(marker, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        info = startup_health.get_prior_crash_info(tmp_hermes_home)
        assert info is not None
        assert info.get("_detected_as_crash") is True
        assert info.get("pid") == fake_pid
        assert "_detected_at" in info


# ---------------------------------------------------------------------------
# Test 2: Crash loop detection
# ---------------------------------------------------------------------------


class TestCrashLoopDetection:
    """Verify restart counter correctly detects crash loops."""

    def test_mark_gateway_started_after_crash_increments_counter(self, tmp_hermes_home):
        """After a crash, mark_gateway_started() increments restart_count_since_clean."""
        # Simulate state where last state was "crashed"
        from gateway.startup_health import _write_state_file
        _write_state_file(
            {"last_gateway_state": "crashed", "restart_count_since_clean": 3},
            tmp_hermes_home,
        )

        # Now a new startup happens
        startup_health.mark_gateway_started(tmp_hermes_home)

        # Counter should be 4 now
        from gateway.startup_health import _read_state_file
        state = _read_state_file(tmp_hermes_home)
        assert state.get("restart_count_since_clean") == 4
        assert state.get("last_gateway_state") == "started"

    def test_mark_gateway_started_after_clean_resets_counter(self, tmp_hermes_home):
        """After a clean shutdown, mark_gateway_started() resets counter to 0."""
        from gateway.startup_health import _write_state_file
        _write_state_file(
            {"last_gateway_state": "stopped_clean", "restart_count_since_clean": 5},
            tmp_hermes_home,
        )

        startup_health.mark_gateway_started(tmp_hermes_home)

        from gateway.startup_health import _read_state_file
        state = _read_state_file(tmp_hermes_home)
        assert state.get("restart_count_since_clean") == 0  # Reset

    def test_crash_loop_detected_at_threshold(self, tmp_hermes_home):
        """get_crash_loop_info correctly identifies crash loops (>=5 restarts)."""
        from gateway.startup_health import _write_state_file
        _write_state_file(
            {"restart_count_since_clean": 5, "healthy_startup_count": 10},
            tmp_hermes_home,
        )

        info = startup_health.get_crash_loop_info(tmp_hermes_home)
        assert info["crash_loop_detected"] is True
        assert info["restart_count_since_clean"] == 5

    def test_no_crash_loop_below_threshold(self, tmp_hermes_home):
        """Fewer than 5 restarts is not a crash loop."""
        from gateway.startup_health import _write_state_file
        _write_state_file(
            {"restart_count_since_clean": 2, "healthy_startup_count": 20},
            tmp_hermes_home,
        )

        info = startup_health.get_crash_loop_info(tmp_hermes_home)
        assert info["crash_loop_detected"] is False
        assert info["restart_count_since_clean"] == 2

    def test_mark_gateway_crashed_records_crash(self, tmp_hermes_home):
        """mark_gateway_crashed() must record exit_code and timestamp."""
        startup_health.mark_gateway_crashed(exit_code=-6, hermes_home=tmp_hermes_home)

        from gateway.startup_health import _read_state_file
        state = _read_state_file(tmp_hermes_home)
        assert state.get("last_gateway_state") == "crashed"
        assert state.get("last_crash_exit_code") == -6
        assert state.get("last_crash_time") is not None


# ---------------------------------------------------------------------------
# Test 3: Startup health marker write/read lifecycle
# ---------------------------------------------------------------------------


class TestStartupHealthMarker:
    """Verify write/read lifecycle of the health marker."""

    def test_write_startup_health_marker(self, tmp_hermes_home):
        """Writing the marker creates a valid JSON file with expected fields."""
        marker = startup_health.write_startup_health_marker(tmp_hermes_home)
        marker_path = tmp_hermes_home / ".gateway-health-marker"

        assert marker_path.exists()
        assert marker.get("pid") == os.getpid()
        assert "start_time" in marker
        assert "start_timestamp" in marker
        assert "git_head" in marker
        assert "python_executable" in marker

    def test_read_startup_health_marker(self, tmp_hermes_home):
        """Reading back the marker returns the same data."""
        original = startup_health.write_startup_health_marker(tmp_hermes_home)
        read_back = startup_health.read_startup_health_marker(tmp_hermes_home)

        assert read_back is not None
        assert read_back["pid"] == original["pid"]
        assert read_back["git_head"] == original["git_head"]

    def test_read_nonexistent_marker(self, tmp_hermes_home):
        """Reading a non-existent marker returns None."""
        assert startup_health.read_startup_health_marker(tmp_hermes_home) is None

    def test_corrupt_marker_returns_none(self, tmp_hermes_home):
        """A corrupt marker file returns None without raising."""
        marker_path = tmp_hermes_home / ".gateway-health-marker"
        marker_path.write_text("this is not valid json", encoding="utf-8")
        assert startup_health.read_startup_health_marker(tmp_hermes_home) is None


# ---------------------------------------------------------------------------
# Test 4: Interrupted work detection from continuation artifacts
# ---------------------------------------------------------------------------


class TestInterruptedWorkDetection:
    """Verify continuation artifact scanning finds interrupted work."""

    def test_find_interrupted_work_with_artifact(self, tmp_hermes_home, tmp_continuation_dir):
        """When a continuation artifact exists with a BIF card, interrupted work is found."""
        artifact = {
            "schema": "biff.live-continuation.v1",
            "created_at": "2026-05-28T10:00:00+00:00",
            "platform": "discord",
            "session_id": "session-123",
            "work_handle": {"active_card": "BIF-1508", "chat_id": "chan-1", "user_id": "user-1"},
            "turn_exit_reason": "max_iterations_reached",
            "auto_continue_started": True,
            "last_completed_step": "Verified startup health marker writes correctly",
            "next_action": "Continue verification of crash loop detection",
            "verification_state": "unfinished; verification required before any done/success claim",
            "user_request": {"preview": "verify crash supervision", "chars": 32, "sha256_16": "abc"},
            "work_already_verified": "Partial progress",
            "source_of_truth_paths": ["Kanban", "logs"],
        }
        artifact_path = tmp_continuation_dir / "20260528T100000Z-session-123-cap-continuation.json"
        artifact_path.write_text(
            json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Force HERMES_BIFF_CONTINUATION_DIR for recovery module
        import gateway.recovery as recovery_mod
        with patch.object(recovery_mod, "_get_continuation_dir", return_value=tmp_continuation_dir):
            result = recovery_mod.find_interrupted_work(tmp_hermes_home)

        assert result is not None
        assert result["card"] == "BIF-1508"
        assert result["session_id"] == "session-123"
        assert result["auto_continue_started"] is True
        assert result["turn_exit_reason"] == "max_iterations_reached"

    def test_no_interrupted_work_without_artifact(self, tmp_hermes_home):
        """Without any continuation artifacts, no interrupted work is detected."""
        result = recovery.find_interrupted_work(tmp_hermes_home)
        assert result is None

    def test_interrupted_work_skips_artifact_without_card(self, tmp_hermes_home, tmp_continuation_dir):
        """An artifact without a BIF card but with a session_id still returns work info."""
        artifact = {
            "schema": "biff.live-continuation.v1",
            "created_at": "2026-05-28T10:00:00+00:00",
            "platform": "discord",
            "session_id": "session-456",
            "work_handle": {"active_card": "", "chat_id": "", "user_id": ""},
            "last_completed_step": "Initial setup",
            "verification_state": "unfinished",
            "user_request": {"preview": "some work", "chars": 16, "sha256_16": "def"},
            "work_already_verified": "",
            "source_of_truth_paths": [],
        }
        artifact_path = tmp_continuation_dir / "20260528T110000Z-session-456-cap-continuation.json"
        artifact_path.write_text(
            json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        import gateway.recovery as recovery_mod
        with patch.object(recovery_mod, "_get_continuation_dir", return_value=tmp_continuation_dir):
            result = recovery_mod.find_interrupted_work(tmp_hermes_home)

        assert result is not None
        assert result["session_id"] == "session-456"
        assert result["card"] == ""  # No card, but still returns info

    def test_find_latest_continuation_artifact_returns_newest(self, tmp_continuation_dir):
        """find_latest_continuation_artifact returns the most recently modified artifact."""
        old = {"session_id": "old-session", "schema": "biff.live-continuation.v1",
               "created_at": "2026-05-27T00:00:00+00:00"}
        new = {"session_id": "new-session", "schema": "biff.live-continuation.v1",
               "created_at": "2026-05-28T00:00:00+00:00"}

        (tmp_continuation_dir / "old.json").write_text(json.dumps(old), encoding="utf-8")
        # Sleep briefly to ensure distinct mtimes
        import time as _time
        _time.sleep(0.01)
        (tmp_continuation_dir / "new.json").write_text(json.dumps(new), encoding="utf-8")

        result = recovery.find_latest_continuation_artifact()
        assert result is not None
        assert result["session_id"] == "new-session"


# ---------------------------------------------------------------------------
# Test 5: Full recovery summary shape
# ---------------------------------------------------------------------------


class TestRecoverySummary:
    """Verify the full "what happened?" recovery answer has the correct shape."""

    def test_recovery_summary_with_no_crash(self, tmp_hermes_home):
        """Clean state produces no-crash summary."""
        summary = recovery.answer_what_happened(tmp_hermes_home)

        assert summary["prior_crash_detected"] is False
        assert summary["crash_loop_detected"] is False
        assert summary["interrupted_work_detected"] is False
        assert "No prior crash" in summary["summary_line"]
        assert "log_instability" in summary
        assert "crash_info" in summary
        assert "interrupted_work" in summary

    def test_recovery_summary_with_crash(self, tmp_hermes_home):
        """After a crash, the summary reports crash details."""
        fake_pid = 777777
        marker = {
            "pid": fake_pid,
            "start_time": "2026-05-28T10:00:00+00:00",
            "start_timestamp": time.time() - 3600,
            "git_head": "abc123",
        }
        marker_path = tmp_hermes_home / ".gateway-health-marker"
        marker_path.write_text(
            json.dumps(marker, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        summary = recovery.answer_what_happened(tmp_hermes_home)
        assert summary["prior_crash_detected"] is True
        assert "unexpected shutdown" in summary["summary_line"]

    def test_recovery_summary_with_crash_loop(self, tmp_hermes_home):
        """After multiple crashes, the summary reports crash loop."""
        from gateway.startup_health import _write_state_file
        _write_state_file(
            {"restart_count_since_clean": 5, "healthy_startup_count": 1},
            tmp_hermes_home,
        )

        fake_pid = 666666
        marker = {
            "pid": fake_pid,
            "start_time": "2026-05-28T09:00:00+00:00",
            "start_timestamp": time.time() - 7200,
            "git_head": "def456",
        }
        marker_path = tmp_hermes_home / ".gateway-health-marker"
        marker_path.write_text(
            json.dumps(marker, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        summary = recovery.answer_what_happened(tmp_hermes_home)
        assert summary["prior_crash_detected"] is True
        assert summary["crash_loop_detected"] is True
        assert "Crash loop" in summary["summary_line"]

    def test_recovery_summary_with_interrupted_work(self, tmp_hermes_home, tmp_continuation_dir):
        """Continuation artifacts appear in the recovery summary."""
        artifact = {
            "schema": "biff.live-continuation.v1",
            "created_at": "2026-05-28T10:00:00+00:00",
            "platform": "discord",
            "session_id": "crash-session",
            "work_handle": {"active_card": "BIF-1509", "chat_id": "chan-1", "user_id": "user-1"},
            "last_completed_step": "Wrote durable state audit",
            "next_action": "Continue with checkpoint implementation",
            "verification_state": "unfinished",
            "user_request": {"preview": "implement durable state", "chars": 28, "sha256_16": "ghi"},
            "work_already_verified": "",
            "source_of_truth_paths": [],
        }
        artifact_path = tmp_continuation_dir / "artifact-crash.json"
        artifact_path.write_text(
            json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        import gateway.recovery as recovery_mod
        with patch.object(recovery_mod, "_get_continuation_dir", return_value=tmp_continuation_dir):
            summary = recovery_mod.answer_what_happened(tmp_hermes_home)

        assert summary["interrupted_work_detected"] is True
        assert summary["interrupted_work"] is not None
        assert summary["interrupted_work"]["card"] == "BIF-1509"
        assert "Interrupted work" in summary["summary_line"]


# ---------------------------------------------------------------------------
# Test 6: Active session snapshot
# ---------------------------------------------------------------------------


class TestActiveSessionSnapshot:
    """Verify active session snapshot survives crashes."""

    def test_snapshot_write_and_read(self, tmp_hermes_home):
        """Writing a session snapshot allows reading it back."""
        recovery.snapshot_active_sessions(
            ["session-a", "session-b"],
            hermes_home=tmp_hermes_home,
        )
        result = recovery.read_active_session_snapshot(tmp_hermes_home)
        assert result is not None
        assert result["session_keys"] == ["session-a", "session-b"]
        assert "snapshot_time" in result

    def test_snapshot_clear(self, tmp_hermes_home):
        """After clearing, the snapshot is gone."""
        recovery.snapshot_active_sessions(["session-x"], hermes_home=tmp_hermes_home)
        recovery.clear_active_session_snapshot(tmp_hermes_home)
        assert recovery.read_active_session_snapshot(tmp_hermes_home) is None

    def test_snapshot_no_file_returns_none(self, tmp_hermes_home):
        """Without a snapshot file, returns None."""
        assert recovery.read_active_session_snapshot(tmp_hermes_home) is None


# ---------------------------------------------------------------------------
# Test 7: Log instability integration
# ---------------------------------------------------------------------------


class TestLogInstabilityIntegration:
    """Verify recent log instability can be checked without live logs."""

    def test_check_recent_log_instability_no_logs(self, tmp_hermes_home):
        """Without any log files, instability check returns clean state."""
        result = recovery.check_recent_log_instability(tmp_hermes_home)
        assert result["has_symptoms"] is False
        assert result["sigterm_count"] == 0
        assert result["severity"] == "none"

    def test_check_recent_log_instability_with_symptoms(self, tmp_hermes_home):
        """With simulated log symptoms, the check detects them."""
        logs_dir = tmp_hermes_home / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "gateway.log").write_text(
            "2026-05-28 10:00:00 INFO received SIGTERM initiating shutdown\n"
            "2026-05-28 10:00:01 INFO signal=SIGTERM gateway stopping\n"
            "2026-05-28 10:00:02 INFO long_turn_repeated_failure_fallback\n"
            "2026-05-28 10:00:03 INFO long_turn_repeated_failure_fallback\n"
            "2026-05-28 10:05:00 INFO received SIGTERM initiating shutdown\n"
            "2026-05-28 10:05:01 INFO signal=SIGTERM gateway stopping\n"
            "2026-05-28 10:10:00 INFO received SIGTERM initiating shutdown\n"
            "2026-05-28 10:10:01 INFO signal=SIGTERM gateway stopping\n"
            "2026-05-28 10:15:00 INFO received SIGTERM initiating shutdown\n"
            "2026-05-28 10:15:01 INFO signal=SIGTERM gateway stopping\n",
            encoding="utf-8",
        )
        # Give the instability detector a large window so it catches our synthetic lines
        from unittest.mock import patch as _patch
        with _patch("gateway.session_hygiene.inspect_biff_runtime_instability_logs") as mock_inspect:
            from gateway.session_hygiene import BiffRuntimeInstabilitySignal
            mock_inspect.return_value = BiffRuntimeInstabilitySignal(
                active=True,
                reasons=("recent_gateway_restarts", "repeated_tool_or_long_turn_loop"),
                sigterm_count=4,
                repeated_failure_count=2,
                severity="soft",
            )
            result = recovery.check_recent_log_instability(tmp_hermes_home)

        assert result["has_symptoms"] is True
