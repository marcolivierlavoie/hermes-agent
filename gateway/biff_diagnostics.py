"""Short-lived Biff Discord diagnostic recorder.

The recorder is intentionally tiny and dependency-free so it can be called from
gateway and tool modules without creating import cycles.  It is enabled by a
timestamp file and writes JSONL events that are easy to summarize later.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


TOKENISH_RE = re.compile(
    r"\b(?:xox[baprs]-|xapp-|sk-[A-Za-z0-9]|ghp_|github_pat_|glpat-)[A-Za-z0-9_.=-]{12,}"
)


def _home() -> Path:
    return Path(os.getenv("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def _until_path() -> Path:
    return Path(os.getenv("HERMES_BIFF_DIAGNOSTICS_UNTIL_FILE") or _home() / "runtime" / "biff-diagnostics-until")


def diagnostics_path() -> Path:
    return Path(os.getenv("HERMES_BIFF_DIAGNOSTICS_PATH") or _home() / "runtime" / "biff-discord-diagnostics.jsonl")


def _enabled_until_epoch() -> float | None:
    raw = os.getenv("HERMES_BIFF_DIAGNOSTICS_UNTIL")
    if not raw:
        try:
            raw = _until_path().read_text(encoding="utf-8").strip()
        except Exception:
            raw = ""
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def diagnostics_enabled(now: float | None = None) -> bool:
    until = _enabled_until_epoch()
    if until is None:
        return False
    return float(now if now is not None else time.time()) <= until


def redact(value: Any, *, max_chars: int = 2000) -> Any:
    if isinstance(value, str):
        text = TOKENISH_RE.sub("[REDACTED_TOKEN]", value)
        if len(text) > max_chars:
            text = text[: max_chars - 40].rstrip() + f"...[truncated {len(text) - max_chars} chars]"
        return text
    if isinstance(value, Mapping):
        return {str(k): redact(v, max_chars=max_chars) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, max_chars=max_chars) for v in value[:50]]
    return value


def text_fingerprint(value: Any) -> dict[str, Any]:
    text = str(value or "")
    return {
        "chars": len(text),
        "sha256_16": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16],
        "preview": redact(text[:500], max_chars=500),
    }


def record_biff_diagnostic(event: str, data: Mapping[str, Any] | None = None) -> None:
    if not diagnostics_enabled():
        return
    now = time.time()
    row = {
        "ts": now,
        "iso": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "event": str(event or "unknown"),
        **(redact(dict(data or {})) if data else {}),
    }
    path = diagnostics_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    except Exception:
        # Diagnostics must never break the gateway path.
        return
