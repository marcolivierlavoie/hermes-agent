"""Gateway recovery helpers for crash-safe supervision.

This module provides the recovery orchestration for interrupted turns
and fresh restarts. It helps Biff answer "what happened?" after a
crash/restart without guessing.

It integrates with:
- ``gateway/startup_health.py`` — crash markers and state persistence
- ``gateway/continuation_artifacts.py`` — continuation artifact read-back
- ``gateway/session_hygiene.py`` — instability signal detection from logs
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _get_hermes_home() -> Path:
    return Path(
        os.getenv("HERMES_HOME") or str(Path.home() / ".hermes")
    ).expanduser().resolve()


def _get_continuation_dir(hermes_home: Optional[Path] = None) -> Path:
    """Return the continuation artifacts directory."""
    override = os.getenv("HERMES_BIFF_CONTINUATION_DIR")
    if override:
        return Path(override)
    base = hermes_home or _get_hermes_home()
    return base / "working-set" / "live-continuations"


def _get_log_dir(hermes_home: Optional[Path] = None) -> Path:
    base = hermes_home or _get_hermes_home()
    return base / "logs"


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Continuation artifact scan
# ---------------------------------------------------------------------------


def find_latest_continuation_artifact(
    hermes_home: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Find the most recent continuation artifact on disk.

    Scans ``{HERMES_HOME}/working-set/live-continuations/*.json``
    and returns the most recently modified artifact's parsed content,
    or ``None`` if none exist.
    """
    cont_dir = _get_continuation_dir(hermes_home)
    if not cont_dir.exists():
        return None
    candidates = sorted(cont_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    latest = candidates[0]
    try:
        raw = latest.read_text(encoding="utf-8").strip()
        return json.loads(raw) if raw else None
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Failed to read continuation artifact %s: %s", latest, e)
        return None


def find_all_continuation_artifacts(
    hermes_home: Optional[Path] = None,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the N most recent continuation artifacts, newest first.

    Each element is the parsed JSON dict with the artifact metadata.
    """
    cont_dir = _get_continuation_dir(hermes_home)
    if not cont_dir.exists():
        return []
    candidates = sorted(cont_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    results: list[dict[str, Any]] = []
    for path in candidates[:limit]:
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if raw:
                results.append(json.loads(raw))
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("Failed to read continuation artifact %s: %s", path, e)
    return results


# ---------------------------------------------------------------------------
# Interrupted work detection
# ---------------------------------------------------------------------------


def find_interrupted_work(
    hermes_home: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Detect interrupted work by scanning continuation artifacts.

    Returns a dict with the interrupted work details, or ``None`` if
    no interrupted work is found.

    The result includes::
        card (str): BIF card identifier
        last_step (str): what was last known completed
        next_action (str): what the continuation artifact says to do next
        session_id (str): which session was affected
        artifact_created (str): ISO timestamp
        auto_continue_started (bool): whether auto-resume was triggered
    """
    artifact = find_latest_continuation_artifact(hermes_home)
    if not artifact:
        return None
    work_handle = artifact.get("work_handle", {})
    card = work_handle.get("active_card", "") if isinstance(work_handle, dict) else ""
    session_id = artifact.get("session_id", "")
    if not card and not session_id:
        return None  # No actionable interrupted work
    return {
        "card": card,
        "last_step": artifact.get("last_completed_step", "Unknown"),
        "next_action": artifact.get("next_action", ""),
        "session_id": session_id,
        "artifact_created": artifact.get("created_at", ""),
        "auto_continue_started": bool(artifact.get("auto_continue_started", False)),
        "turn_exit_reason": artifact.get("turn_exit_reason", ""),
        "platform": artifact.get("platform", ""),
        "user_request_preview": artifact.get("user_request", {}).get("preview", ""),
        "verification_state": artifact.get("verification_state", ""),
    }


# ---------------------------------------------------------------------------
# Log-based instability check
# ---------------------------------------------------------------------------


def check_recent_log_instability(
    hermes_home: Optional[Path] = None,
    *,
    max_chars_per_file: int = 80_000,
) -> dict[str, Any]:
    """Check recent gateway/error logs for crash/restart symptoms.

    Returns a dict with::
        has_symptoms (bool): whether log instability signals fired
        sigterm_count (int): SIGTERM mentions in recent logs
        error_keywords (list[str]): matched error patterns
    """
    from gateway.session_hygiene import inspect_biff_runtime_instability_logs
    signal = inspect_biff_runtime_instability_logs(
        log_dir=_get_log_dir(hermes_home),
        max_chars_per_file=max_chars_per_file,
    )
    return {
        "has_symptoms": signal.active,
        "sigterm_count": signal.sigterm_count,
        "repeated_failure_count": signal.repeated_failure_count,
        "codex_empty_output_count": signal.codex_empty_output_count,
        "severity": signal.severity,
        "reasons": list(signal.reasons),
    }


# ---------------------------------------------------------------------------
# Full "what happened?" answer
# ---------------------------------------------------------------------------


def answer_what_happened(
    hermes_home: Optional[Path] = None,
) -> dict[str, Any]:
    """Produce the full "what happened?" answer for Biff after a restart.

    This is the main recovery entry point. It combines:
    1. Crash detection from health marker
    2. Crash loop detection from state file
    3. Interrupted work from continuation artifacts
    4. Log instability signals

    Returns:
        dict with keys::
            prior_crash_detected (bool)
            crash_loop_detected (bool)
            interrupted_work_detected (bool)
            crash_info (dict|None)
            interrupted_work (dict|None)
            log_instability (dict)
            summary_line (str): Human-readable summary
    """
    from gateway.startup_health import (
        gateway_recovery_summary,
    )

    # Crash/restart detection
    startup_info = gateway_recovery_summary(hermes_home)

    # Interrupted work
    interrupted = find_interrupted_work(hermes_home)

    # Log instability
    log_symptoms = check_recent_log_instability(hermes_home)

    # Build human-readable summary
    parts: list[str] = []
    if startup_info.get("prior_crash_detected"):
        parts.append("Gateway had an unexpected shutdown")
        crash_info = startup_info.get("crash_info") or {}
        if crash_info:
            parts.append(f"(PID {crash_info.get('pid')}, started {crash_info.get('start_time')})")
    if startup_info.get("crash_loop_detected"):
        rc = startup_info.get("crash_loop_info", {}).get("restart_count_since_clean", 0)
        parts.append(f"Crash loop detected ({rc} consecutive restarts)")
    if interrupted:
        parts.append(
            f"Interrupted work on {interrupted.get('card') or interrupted.get('session_id')}"
        )
    if log_symptoms.get("has_symptoms"):
        reasons = log_symptoms.get("reasons", [])
        severity = log_symptoms.get("severity", "none")
        parts.append(f"Log instability symptoms detected: {', '.join(reasons)} (severity={severity})")

    summary_line = "; ".join(parts) if parts else "No prior crash or interrupted work detected"

    return {
        "prior_crash_detected": startup_info.get("prior_crash_detected", False),
        "crash_loop_detected": startup_info.get("crash_loop_detected", False),
        "interrupted_work_detected": interrupted is not None,
        "crash_info": startup_info.get("crash_info"),
        "crash_loop_info": startup_info.get("crash_loop_info"),
        "interrupted_work": interrupted,
        "log_instability": log_symptoms,
        "summary_line": summary_line,
    }


# ---------------------------------------------------------------------------
# Active session snapshot (for crash survival)
# ---------------------------------------------------------------------------


def _get_active_session_snapshot_path(hermes_home: Optional[Path] = None) -> Path:
    base = hermes_home or _get_hermes_home()
    return base / "active-sessions-snapshot.json"


def snapshot_active_sessions(
    session_keys: list[str],
    hermes_home: Optional[Path] = None,
) -> None:
    """Persist a snapshot of active session keys for crash survival.

    On restart, Biff can inspect this snapshot to know which sessions
    had active agents when the gateway went down.
    """
    path = _get_active_session_snapshot_path(hermes_home)
    payload = {
        "snapshot_time": _utc_now_iso(),
        "session_keys": session_keys,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_active_session_snapshot(
    hermes_home: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Read the active session snapshot, or None if absent/corrupt."""
    path = _get_active_session_snapshot_path(hermes_home)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return json.loads(raw) if raw else None
    except (OSError, json.JSONDecodeError):
        return None


def clear_active_session_snapshot(
    hermes_home: Optional[Path] = None,
) -> None:
    """Remove the active session snapshot after a clean recovery."""
    path = _get_active_session_snapshot_path(hermes_home)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass