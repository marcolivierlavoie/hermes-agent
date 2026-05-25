"""Shared gateway restart constants and parsing helpers."""

from hermes_cli.config import DEFAULT_CONFIG

# EX_TEMPFAIL from sysexits.h — used to ask the service manager to restart
# the gateway after a graceful drain/reload path completes.
GATEWAY_SERVICE_RESTART_EXIT_CODE = 75

DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT = float(
    DEFAULT_CONFIG["agent"]["restart_drain_timeout"]
)

# Detached /restart watcher should not wait indefinitely for the old gateway
# PID to disappear.  A clean teardown writes an explicit readiness marker; the
# watcher starts the replacement as soon as that marker appears even if a
# non-daemon thread/subprocess keeps the old Python process alive briefly.
DEFAULT_GATEWAY_DETACHED_RESTART_MAX_WAIT = 30.0


def parse_restart_drain_timeout(raw: object) -> float:
    """Parse a configured drain timeout, falling back to the shared default."""
    try:
        value = float(raw) if str(raw or "").strip() else DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    except (TypeError, ValueError):
        return DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    return max(0.0, value)


def parse_detached_restart_max_wait(raw: object) -> float:
    """Parse the detached restart watcher max wait in seconds."""
    try:
        value = (
            float(str(raw))
            if str(raw or "").strip()
            else DEFAULT_GATEWAY_DETACHED_RESTART_MAX_WAIT
        )
    except (TypeError, ValueError):
        return DEFAULT_GATEWAY_DETACHED_RESTART_MAX_WAIT
    return max(0.0, value)
