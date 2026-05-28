"""Startup health markers for crash-safe gateway supervision.

This module provides a lightweight crash marker system that allows
the gateway to detect, on startup, whether the previous run ended
cleanly or crashed. This is a core building block of BIF-1508's
idempotent startup guards and restart-loop detection.

Design:
- On clean startup: write a JSON marker file with PID, start time,
  git HEAD, and module paths.
- On clean shutdown: remove the marker file.
- On crash: the marker file survives — the next startup reads it
  to determine that the previous run did not exit cleanly.
- A crash counter is maintained in the marker so Biff can detect
  restart loops (N crashes without a clean exit in M minutes).

Marker path:
  ``{HERMES_HOME}/.gateway-health-marker``

State file (persistent, survives across runs):
  ``{HERMES_HOME}/gateway_state.json``
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MARKER_FILENAME = ".gateway-health-marker"
_STATE_FILENAME = "gateway_state.json"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _get_hermes_home() -> Path:
    """Return HERMES_HOME path (env var override or ~/.hermes)."""
    return Path(
        os.getenv("HERMES_HOME") or str(Path.home() / ".hermes")
    ).expanduser().resolve()


def _get_marker_path(hermes_home: Optional[Path] = None) -> Path:
    return (hermes_home or _get_hermes_home()) / _MARKER_FILENAME


def _get_state_path(hermes_home: Optional[Path] = None) -> Path:
    return (hermes_home or _get_hermes_home()) / _STATE_FILENAME


# ---------------------------------------------------------------------------
# Git HEAD helper
# ---------------------------------------------------------------------------


def _get_git_head(repo_root: Optional[Path] = None) -> str:
    """Get the current git HEAD hash, or 'unknown' if not in a repo."""
    base = repo_root or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(base),
        )
        if result.returncode == 0:
            return result.stdout.strip()[:40]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Health marker
# ---------------------------------------------------------------------------


def write_startup_health_marker(
    hermes_home: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Write the gateway health marker on successful startup.

    Returns the marker dict. Raises on write failure.
    """
    marker = {
        "pid": os.getpid(),
        "start_time": datetime.now(timezone.utc).isoformat(),
        "start_timestamp": time.time(),
        "git_head": _get_git_head(repo_root),
        "python_executable": sys.executable,
        "cwd": str(Path.cwd()),
        "module_paths": list(sys.path),
    }
    path = _get_marker_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(marker, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote startup health marker: %s", path)
    return marker


def remove_startup_health_marker(hermes_home: Optional[Path] = None) -> bool:
    """Remove the health marker on clean shutdown.

    Returns True if the marker was removed, False if it didn't exist.
    """
    path = _get_marker_path(hermes_home)
    if path.exists():
        try:
            path.unlink(missing_ok=True)
            logger.info("Removed startup health marker: %s", path)
            return True
        except OSError as e:
            logger.warning("Failed to remove health marker %s: %s", path, e)
            return False
    return True  # Nothing to remove — vacuously true


def read_startup_health_marker(
    hermes_home: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Read the health marker, returning None if absent or corrupt."""
    path = _get_marker_path(hermes_home)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Failed to read health marker %s: %s", path, e)
    return None


def detect_prior_crash(hermes_home: Optional[Path] = None) -> bool:
    """Return True if the gateway's previous run ended in a crash.

    Logic: if the health marker exists, the previous startup never
    cleaned it up (clean shutdown removes it). Therefore the previous
    run crashed or was killed unexpectedly.
    """
    marker = read_startup_health_marker(hermes_home)
    if marker is None:
        return False
    # Marker exists — the previous run started but did not clean up.
    # Validate that it's from a different process (our PID).
    marker_pid = marker.get("pid")
    return marker_pid != os.getpid()


def get_prior_crash_info(
    hermes_home: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Return info about the prior crash, or None if no crash detected.

    Returns the health marker dict from the crashed run, enriched with
    a ``_detected_as_crash`` boolean.
    """
    marker = read_startup_health_marker(hermes_home)
    if marker is None:
        return None
    marker_pid = marker.get("pid")
    if marker_pid == os.getpid():
        return None  # Same process — not a crash, just rechecking
    marker["_detected_as_crash"] = True
    marker["_detected_at"] = datetime.now(timezone.utc).isoformat()
    return marker


# ---------------------------------------------------------------------------
# Gateway state file (persistent across runs)
# ---------------------------------------------------------------------------


def _read_state_file(hermes_home: Optional[Path] = None) -> dict[str, Any]:
    """Read the persistent gateway state file, returning defaults if absent."""
    path = _get_state_path(hermes_home)
    if not path.exists():
        return {
            "restart_count_since_clean": 0,
            "healthy_startup_count": 0,
            "last_gateway_state": "never_started",
            "last_crash_time": None,
            "last_crash_exit_code": None,
            "last_restart_time": None,
            "last_clean_shutdown_time": None,
        }
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Failed to read state file %s: %s", path, e)
    return {}


def _write_state_file(
    state: dict[str, Any],
    hermes_home: Optional[Path] = None,
) -> None:
    """Atomically write the persistent gateway state file."""
    path = _get_state_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Write atomically
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def mark_gateway_started(hermes_home: Optional[Path] = None) -> None:
    """Update the gateway state file on successful startup.

    This is idempotent — call it once after all adapters are connected.
    """
    state = _read_state_file(hermes_home)
    # If last state was not 'stopped_clean', this startup follows a crash
    if state.get("last_gateway_state") not in (None, "stopped_clean", "never_started"):
        state["restart_count_since_clean"] = state.get("restart_count_since_clean", 0) + 1
    else:
        state["restart_count_since_clean"] = 0  # Reset on clean-cycle start
    state["healthy_startup_count"] = state.get("healthy_startup_count", 0) + 1
    state["last_gateway_state"] = "started"
    state["pid"] = os.getpid()
    _write_state_file(state, hermes_home)
    logger.info(
        "Gateway state: started (crash-count-since-clean=%s, total=%s)",
        state["restart_count_since_clean"],
        state["healthy_startup_count"],
    )


def mark_gateway_stopped_clean(hermes_home: Optional[Path] = None) -> None:
    """Update the gateway state file on clean shutdown."""
    state = _read_state_file(hermes_home)
    state["last_gateway_state"] = "stopped_clean"
    state["last_clean_shutdown_time"] = datetime.now(timezone.utc).isoformat()
    state["restart_count_since_clean"] = 0
    _write_state_file(state, hermes_home)
    logger.info("Gateway state: stopped_clean")


def mark_gateway_crashed(
    exit_code: Optional[int] = None,
    hermes_home: Optional[Path] = None,
) -> None:
    """Update the gateway state file to record a crash.

    This is called on startup when a prior crash is detected.
    """
    state = _read_state_file(hermes_home)
    state["last_gateway_state"] = "crashed"
    state["last_crash_time"] = datetime.now(timezone.utc).isoformat()
    state["last_crash_exit_code"] = exit_code
    _write_state_file(state, hermes_home)
    logger.warning("Gateway state: crashed (exit_code=%s)", exit_code)


def get_crash_loop_info(hermes_home: Optional[Path] = None) -> dict[str, Any]:
    """Return structured info about crash-loop status.

    Returns:
        dict with keys::
            crash_loop_detected (bool): True if restart count >= 5
            restart_count_since_clean (int): consecutive restarts w/o clean exit
            healthy_startup_count (int): total healthy starts
            last_crash_time (str|None): ISO timestamp of last crash
    """
    state = _read_state_file(hermes_home)
    restart_count = state.get("restart_count_since_clean", 0)
    return {
        "crash_loop_detected": restart_count >= 5,
        "restart_count_since_clean": restart_count,
        "healthy_startup_count": state.get("healthy_startup_count", 0),
        "last_crash_time": state.get("last_crash_time"),
        "last_gateway_state": state.get("last_gateway_state"),
    }


# ---------------------------------------------------------------------------
# Full recovery procedure
# ---------------------------------------------------------------------------


def gateway_recovery_summary(
    hermes_home: Optional[Path] = None,
) -> dict[str, Any]:
    """Produce a structured "what happened?" summary after a restart.

    This is the main entry point for Biff to determine:
    - Did the previous run crash?
    - Is there a crash loop?
    - What was the last known good state?

    Returns:
        dict with keys::
            prior_crash_detected (bool)
            crash_loop_detected (bool)
            crash_info (dict|None): health marker from crashed run
            crash_loop_info (dict): crash-loop status details
            state_summary (dict): full state file contents
    """
    crash_info = get_prior_crash_info(hermes_home)
    loop_info = get_crash_loop_info(hermes_home)
    state = _read_state_file(hermes_home)
    return {
        "prior_crash_detected": crash_info is not None,
        "crash_loop_detected": loop_info.get("crash_loop_detected", False),
        "crash_info": crash_info,
        "crash_loop_info": loop_info,
        "state_summary": state,
    }