"""Continuation artifact helpers for live Discord cap events.

These helpers are pure/best-effort so the gateway can preserve resumable state
without making a live cap event worse.  Artifacts are compact JSON + Markdown
files under the Hermes working set and intentionally contain fingerprints and
short summaries rather than raw transcripts or secrets.
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

_TOKENISH_RE = re.compile(
    r"\b(?:xox[baprs]-|xapp-|sk-[A-Za-z0-9]|ghp_|github_pat_|glpat-|AIza)[A-Za-z0-9_.=-]{12,}"
)


def _home() -> Path:
    return Path(os.getenv("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def continuation_dir() -> Path:
    return Path(os.getenv("HERMES_BIFF_CONTINUATION_DIR") or _home() / "working-set" / "live-continuations")


def _redact_text(value: Any, limit: int = 1600) -> str:
    text = str(value or "").strip()
    text = _TOKENISH_RE.sub("[REDACTED_TOKEN]", text)
    if len(text) > limit:
        return text[: max(0, limit - 40)].rstrip() + f"\n...[trimmed {len(text) - limit} chars]"
    return text


def _fingerprint(value: Any) -> dict[str, Any]:
    text = str(value or "")
    return {
        "chars": len(text),
        "sha256_16": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16],
        "preview": _redact_text(text, 500),
    }


def build_continuation_artifact(
    *,
    user_request: Any,
    agent_result: Mapping[str, Any] | None,
    session_id: str | None,
    platform: str | None,
    source: Mapping[str, Any] | None = None,
    guardrail_settings: Mapping[str, Any] | None = None,
    auto_continue: bool = False,
    next_role: str | None = None,
    next_command: str | None = None,
) -> dict[str, Any]:
    """Build a compact resumable handoff for a live cap-risk/cap-hit event."""

    result = dict(agent_result or {})
    now = time.time()
    known = _redact_text(result.get("final_response") or result.get("error") or "", 1800)
    remaining = "Resume from the artifact, verify source-of-truth state, finish remaining checks, and do not claim done without visible evidence."
    return {
        "schema": "biff.live-continuation.v1",
        "created_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "platform": str(platform or ""),
        "session_id": str(session_id or ""),
        "turn_exit_reason": str(result.get("turn_exit_reason") or "cap_risk_or_hit"),
        "auto_continue_started": bool(auto_continue),
        "user_request": _fingerprint(user_request),
        "work_already_verified": known or "No useful final summary was produced before the cap event.",
        "remaining_checks": remaining,
        "source_of_truth_paths": [
            "Hermes Kanban board biff-os for card/task state",
            "Hermes session transcript for full raw turn history",
            "This continuation artifact for compact resumable context",
        ],
        "next_role": next_role or "forge/vex/ranger as routed by the original request",
        "next_command": next_command or "Continue the same request from this continuation artifact with fresh budget.",
        "guardrail_settings": dict(guardrail_settings or {}),
        "source": dict(source or {}),
        "metrics": {
            "api_calls": result.get("api_calls"),
            "completed": result.get("completed"),
            "failed": result.get("failed"),
            "partial": result.get("partial"),
            "interrupted": result.get("interrupted"),
        },
    }


def write_continuation_artifact(**kwargs: Any) -> dict[str, Any]:
    """Write JSON + Markdown continuation files and return their paths.

    Exceptions intentionally propagate to the caller so tests can assert on
    failure. Gateway integration catches errors because artifact writing is
    best-effort there.
    """

    artifact = build_continuation_artifact(**kwargs)
    out_dir = continuation_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session = re.sub(r"[^A-Za-z0-9_.-]+", "-", artifact.get("session_id") or "session")[:80]
    stem = f"{stamp}-{session}-cap-continuation"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    artifact["artifact_paths"] = {"json": str(json_path), "markdown": str(md_path)}
    json_path.write_text(json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    md = [
        "# Biff live continuation artifact",
        "",
        f"Created: {artifact['created_at']}",
        f"Platform/session: {artifact['platform']} / {artifact['session_id']}",
        f"Exit reason: {artifact['turn_exit_reason']}",
        f"Auto-continue started: {artifact['auto_continue_started']}",
        "",
        "## User request fingerprint",
        json.dumps(artifact["user_request"], indent=2, sort_keys=True, ensure_ascii=False),
        "",
        "## Work already verified / visible summary",
        artifact["work_already_verified"],
        "",
        "## Remaining checks",
        artifact["remaining_checks"],
        "",
        "## Source of truth paths",
        *[f"- {p}" for p in artifact["source_of_truth_paths"]],
        "",
        "## Next role / command",
        f"Role: {artifact['next_role']}",
        f"Command: {artifact['next_command']}",
    ]
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return artifact
