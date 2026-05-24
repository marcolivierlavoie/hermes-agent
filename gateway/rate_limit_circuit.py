"""Small persistent circuit breaker for Biff live-chat provider limits."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home


DEFAULT_COOLDOWN_SECONDS = 15 * 60
_RATE_LIMIT_RE = re.compile(
    r"\b(usage[_\s-]?limit[_\s-]?reached|rate[_\s-]?limit|too many requests|quota|429|credits? exhausted)\b",
    re.IGNORECASE,
)


def _path() -> Path:
    root = get_hermes_home() / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root / "biff-rate-limit-circuit.json"


def looks_like_provider_rate_limit(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, Mapping):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    return bool(_RATE_LIMIT_RE.search(text))


def result_has_provider_rate_limit(result: Any) -> bool:
    """Return true only for error-like provider limit signals.

    Normal assistant text can mention quotas, rate limits, backups, or setup
    steps without meaning the provider rejected the turn. Keep the live-chat
    circuit tied to error fields so ordinary answers do not pause Discord.
    """

    if not isinstance(result, Mapping):
        return looks_like_provider_rate_limit(result)

    error_payload = {
        "error": result.get("error"),
        "exception": result.get("exception"),
        "provider_error": result.get("provider_error"),
        "turn_exit_reason": result.get("turn_exit_reason"),
        "status_code": result.get("status_code"),
    }
    if any(value is not None for value in error_payload.values()):
        return looks_like_provider_rate_limit(error_payload)
    return False


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _write(data: Mapping[str, Any]) -> None:
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(data), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(_path())


def record_rate_limit(
    *,
    scope: str = "discord",
    reason: Any = None,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    now: float | None = None,
) -> dict[str, Any]:
    now_ts = float(time.time() if now is None else now)
    until = now_ts + max(1, int(cooldown_seconds))
    data = _read()
    data[str(scope or "default")] = {
        "until": until,
        "recorded_at": now_ts,
        "reason": str(reason or "")[:500],
    }
    _write(data)
    return data[str(scope or "default")]


def active_rate_limit(
    *,
    scope: str = "discord",
    now: float | None = None,
) -> dict[str, Any] | None:
    now_ts = float(time.time() if now is None else now)
    data = _read()
    entry = data.get(str(scope or "default"))
    if not isinstance(entry, Mapping):
        return None
    try:
        until = float(entry.get("until") or 0)
    except Exception:
        until = 0
    if until <= now_ts:
        data.pop(str(scope or "default"), None)
        try:
            _write(data)
        except Exception:
            pass
        return None
    remaining = max(1, int(until - now_ts))
    return {**dict(entry), "remaining_seconds": remaining}


def clear_rate_limit(scope: str = "discord") -> None:
    data = _read()
    if data.pop(str(scope or "default"), None) is not None:
        _write(data)
