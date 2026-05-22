#!/usr/bin/env python3
"""Small unscheduled watchdog for Uptime Kuma CT 130 remediation readiness.

Checks the Tailnet Uptime Kuma endpoint and, when it appears down, can invoke
BIF-669's gated remediation runner for the exact uptimekuma_ct_down rule.

Safety defaults:
- not installed/scheduled by this script;
- remediation is opt-in via UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE=1;
- kill switch UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE=1 wins;
- cooldown prevents restart loops;
- all production execution still goes through scripts/watchdog-remediate.py and
  scripts/restart-uptimekuma-ct130.sh with --allow-production.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "https://biff.tail460c2.ts.net:3001/"
STATE_PATH = Path(os.environ.get("UPTIME_KUMA_CT130_WATCHDOG_STATE", "~/.hermes/state/uptime_kuma_ct130_watchdog.json")).expanduser()
REMEDIATION_RULE = "uptimekuma_ct_down"
REMEDIATION_TARGET = "uptimekuma_ct_130"
REMEDIATION_DISABLE_ENV = "UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_DISABLE"
REMEDIATION_ENABLE_ENV = "UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_ENABLE"
REMEDIATION_GLOBAL_ENABLE_ENV = "BIF669_WATCHDOG_REMEDIATION_ENABLE"
REMEDIATION_COOLDOWN_SECONDS = int(os.environ.get("UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_COOLDOWN_SECONDS", "1800"))
REMEDIATION_RUNNER = Path(
    os.environ.get(
        "UPTIME_KUMA_CT130_WATCHDOG_REMEDIATION_RUNNER",
        "/Users/marco/.hermes/hermes-agent-biff-runtime/scripts/watchdog-remediate.py",
    )
).expanduser()

SECRET_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
    (re.compile(r"[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}"), "[REDACTED_TOKEN]"),
]


def sanitize(value: Any, max_len: int = 240) -> str:
    text = "" if value is None else str(value)
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    text = text.replace("\n", " ").strip()
    return text[:max_len]


def sanitize_json(value: Any, max_len: int = 4000) -> Any:
    if isinstance(value, dict):
        return {sanitize(str(k), 120): sanitize_json(v, max_len=max_len) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item, max_len=max_len) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize(value, max_len=max_len)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_PATH)


def remediation_disabled() -> bool:
    return os.environ.get(REMEDIATION_DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def remediation_enabled() -> bool:
    return os.environ.get(REMEDIATION_ENABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def check_endpoint(url: str, timeout: float) -> tuple[bool, str, int | None]:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "biff-uptime-kuma-ct130-watchdog/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(256)
            code = int(response.status)
            return 200 <= code <= 399, f"HTTP {code}", code
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
        return 200 <= code <= 399, f"HTTP {code}", code
    except Exception as exc:
        return False, f"{type(exc).__name__}: {sanitize(exc)}", None


def maybe_remediate_uptime_kuma_down(state: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    base: dict[str, Any] = {
        "type": "uptime_kuma_ct130_remediation",
        "rule": REMEDIATION_RULE,
        "target": REMEDIATION_TARGET,
        "live_default": False,
        "enable_env": REMEDIATION_ENABLE_ENV,
        "kill_switch_env": REMEDIATION_DISABLE_ENV,
        "cooldown_seconds": REMEDIATION_COOLDOWN_SECONDS,
    }

    if remediation_disabled():
        return {**base, "status": "disabled_by_env"}
    if not remediation_enabled():
        return {**base, "status": "disabled_until_enabled_env"}

    last_attempt = float(state.get("uptime_kuma_ct130_remediation_last_attempt_at") or 0)
    seconds_until_next = int(last_attempt + REMEDIATION_COOLDOWN_SECONDS - now)
    if seconds_until_next > 0:
        return {**base, "status": "cooldown", "seconds_until_next_attempt": seconds_until_next}

    if not REMEDIATION_RUNNER.exists():
        state["uptime_kuma_ct130_remediation_last_attempt_at"] = now
        return {**base, "status": "runner_missing", "runner": sanitize(REMEDIATION_RUNNER)}

    state["uptime_kuma_ct130_remediation_last_attempt_at"] = now
    command = [
        sys.executable,
        str(REMEDIATION_RUNNER),
        "--rule",
        REMEDIATION_RULE,
        "--event",
        "down",
        "--target",
        REMEDIATION_TARGET,
        "--execute",
        "--allow-rule",
        REMEDIATION_RULE,
        "--allow-production",
    ]
    env = os.environ.copy()
    env.setdefault(REMEDIATION_GLOBAL_ENABLE_ENV, "1")
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=240,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {**base, "status": "timeout", "timeout_seconds": 240}
    except Exception as exc:
        return {**base, "status": "runner_error", "error": f"{type(exc).__name__}: {sanitize(exc)}"}

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    parsed: Any | None = None
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = {"raw_stdout_tail": sanitize(stdout[-2000:], 2000)}

    outcome: dict[str, Any] = {**base, "status": "invoked", "runner_rc": proc.returncode}
    if parsed is not None:
        outcome["runner_stdout"] = sanitize_json(parsed)
    if stderr:
        outcome["runner_stderr_tail"] = sanitize(stderr[-2000:], 2000)
    return outcome


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unscheduled Uptime Kuma CT130 watchdog/remediation primitive")
    parser.add_argument("--url", default=os.environ.get("UPTIME_KUMA_CT130_WATCHDOG_URL", DEFAULT_ENDPOINT))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("UPTIME_KUMA_CT130_WATCHDOG_TIMEOUT", "8")))
    parser.add_argument("--report-current", action="store_true", help="print a current status line even when healthy")
    parser.add_argument("--simulate-down", action="store_true", help="test/smoke only: force a down observation without probing network")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    state = load_state()

    if args.simulate_down:
        ok, detail, code = False, "simulated_down", None
    else:
        ok, detail, code = check_endpoint(args.url, args.timeout)

    current = {
        "type": "uptime_kuma_ct130_watchdog",
        "target": REMEDIATION_TARGET,
        "url": args.url,
        "ok": ok,
        "detail": sanitize(detail),
        "http_code": code,
    }

    previous_ok = state.get("last_ok")
    state["last_ok"] = ok
    state["last_detail"] = sanitize(detail)
    state["last_checked_at"] = int(time.time())

    lines: list[str] = []
    if ok:
        if previous_ok is False:
            lines.append(json.dumps({**current, "status": "recovered"}, sort_keys=True))
        elif args.report_current:
            lines.append(json.dumps({**current, "status": "healthy"}, sort_keys=True))
    else:
        remediation_outcome = maybe_remediate_uptime_kuma_down(state)
        status = "down"
        if previous_ok is None:
            status = "initial_down"
        elif previous_ok is False:
            status = "still_down"
        lines.append(json.dumps({**current, "status": status, "remediation": sanitize_json(remediation_outcome)}, sort_keys=True))

    save_state(state)
    if lines:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
