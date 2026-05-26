#!/usr/bin/env python3
"""Measure Fast Biff /biff/v1/chat continuity with a stable session ID.

The script intentionally reports status/latency/session metadata only; it never
prints the bearer token or full model replies. Use --restart-command to include a
bounded gateway restart between the two chat turns.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


def _request_json(url: str, *, token: str | None = None, payload: dict[str, Any] | None = None, timeout: float = 60.0) -> tuple[int, dict[str, str], Any, float]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
        if payload and payload.get("session_id"):
            headers["X-Hermes-Session-Key"] = str(payload["session_id"])
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = (time.perf_counter() - start) * 1000
            body = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body) if body else None
            except json.JSONDecodeError:
                parsed = {"non_json_prefix": body[:120]}
            return resp.status, dict(resp.headers.items()), parsed, elapsed_ms
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = {"non_json_prefix": body[:120]}
        return e.code, dict(e.headers.items()), parsed, elapsed_ms


def _chat(base_url: str, token: str, session_id: str, message: str, timeout: float) -> dict[str, Any]:
    status, headers, body, latency_ms = _request_json(
        base_url.rstrip("/") + "/biff/v1/chat",
        token=token,
        payload={"session_id": session_id, "message": message},
        timeout=timeout,
    )
    continuity = body.get("continuity") if isinstance(body, dict) else None
    return {
        "status": status,
        "ok": status == 200,
        "latency_ms": round(latency_ms, 1),
        "session_id": body.get("session_id") if isinstance(body, dict) else None,
        "header_session_id": headers.get("X-Hermes-Session-Id"),
        "continuity_present": isinstance(continuity, dict),
        "continuity_session_matches": isinstance(continuity, dict) and continuity.get("session_id") == session_id,
        "message_chars": len(body.get("message") or "") if isinstance(body, dict) else 0,
        "error_code": ((body.get("error") or {}).get("code") if isinstance(body, dict) else None),
    }


def _wait_health(base_url: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    attempts = 0
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        attempts += 1
        try:
            status, _headers, body, latency_ms = _request_json(base_url.rstrip("/") + "/health", timeout=5)
            last = {"status": status, "ok": status == 200, "latency_ms": round(latency_ms, 1), "attempts": attempts}
            if status == 200:
                return last
        except Exception as exc:  # noqa: BLE001 - diagnostic only, no secrets
            last = {"ok": False, "attempts": attempts, "error": type(exc).__name__}
        time.sleep(1)
    return last | {"ok": False, "timed_out": True, "attempts": attempts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("FAST_BIFF_BASE_URL", "http://127.0.0.1:8642"))
    parser.add_argument("--token-env", default="API_SERVER_KEY")
    parser.add_argument("--session-id", default=f"fast-biff-measure:{uuid.uuid4().hex[:10]}")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--restart-command", default="", help="Optional bounded shell command to run between turns.")
    args = parser.parse_args()

    token = os.environ.get(args.token_env)
    if not token:
        print(json.dumps({"ok": False, "error": f"missing env var {args.token_env}"}, indent=2))
        return 2

    result: dict[str, Any] = {
        "base_url": args.base_url,
        "session_id": args.session_id,
        "token_source": args.token_env,
    }
    result["health_before"] = _wait_health(args.base_url, timeout=min(10, args.timeout))
    result["turn_before"] = _chat(
        args.base_url,
        token,
        args.session_id,
        "Fast Biff continuity measurement turn 1. Reply with one short sentence.",
        args.timeout,
    )

    if args.restart_command:
        started = time.perf_counter()
        proc = subprocess.run(args.restart_command, shell=True, text=True, capture_output=True, timeout=args.timeout)
        result["restart"] = {
            "command": shlex.split(args.restart_command)[0] if args.restart_command.strip() else "",
            "returncode": proc.returncode,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "stdout_chars": len(proc.stdout or ""),
            "stderr_chars": len(proc.stderr or ""),
        }
        result["health_after_restart"] = _wait_health(args.base_url, timeout=args.timeout)

    result["turn_after"] = _chat(
        args.base_url,
        token,
        args.session_id,
        "Fast Biff continuity measurement turn 2 after restart/reload. Continue this same session in one short sentence.",
        args.timeout,
    )
    result["success"] = bool(
        result["turn_before"].get("ok")
        and result["turn_after"].get("ok")
        and result["turn_before"].get("session_id") == args.session_id
        and result["turn_after"].get("session_id") == args.session_id
        and result["turn_before"].get("continuity_present")
        and result["turn_after"].get("continuity_present")
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
