#!/usr/bin/env python3
"""Gated watchdog/down-alert remediation runner for BIF-669.

Default behavior is dry-run/no-action. A command can run only when all gates pass:
  1. the matching rule exists in config/watchdog-remediation-rules.json;
  2. the rule action_class is not in blocked_action_classes;
  3. --execute is passed;
  4. BIF669_WATCHDOG_REMEDIATION_ENABLE=1 is present;
  5. --allow-rule <rule_id> is present;
  6. production rules also require --allow-production.

The rules intentionally exclude destructive/power/network/storage actions.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = REPO_ROOT / "config" / "watchdog-remediation-rules.json"
SECRET_MARKERS = ("token", "password", "passwd", "secret", "api_key", "apikey", "authorization", "bearer")


class RemediationError(RuntimeError):
    pass


def load_rules(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("default_mode") != "dry_run":
        raise RemediationError("rules file must default to dry_run")
    if not isinstance(data.get("rules"), list):
        raise RemediationError("rules file missing rules list")
    return data


def find_rule(data: dict[str, Any], rule_id: str) -> dict[str, Any]:
    for rule in data["rules"]:
        if rule.get("id") == rule_id:
            return rule
    raise RemediationError(f"unknown rule: {rule_id}")


def sanitize(value: str) -> str:
    text = value
    lowered = text.lower()
    for marker in SECRET_MARKERS:
        if marker in lowered:
            return "[redacted-sensitive-output]"
    return text


def validate_rule(data: dict[str, Any], rule: dict[str, Any]) -> None:
    allowed = set(data.get("allowed_action_classes", []))
    blocked = set(data.get("blocked_action_classes", []))
    action_class = rule.get("action_class")
    if action_class in blocked:
        raise RemediationError(f"blocked action class: {action_class}")
    if allowed and action_class not in allowed:
        raise RemediationError(f"unsupported action class: {action_class}")
    if rule.get("default_action") != "dry_run":
        raise RemediationError(f"rule {rule.get('id')} must default to dry_run")
    if rule.get("risk") not in {"low", "medium"}:
        raise RemediationError(f"rule {rule.get('id')} has unsupported risk")
    command = rule.get("command")
    if not isinstance(command, list) or not command:
        raise RemediationError(f"rule {rule.get('id')} missing command list")
    first = str(command[0])
    if first.startswith("/") or ".." in Path(first).parts:
        raise RemediationError("commands must be repo-relative and must not traverse directories")
    if any(str(part).startswith("-") for part in command):
        raise RemediationError("command arguments that look like flags are not allowed in rules")


def condition_matches(rule: dict[str, Any], event: str, target: str) -> bool:
    conditions = rule.get("conditions", {})
    return conditions.get("event") == event and conditions.get("target") == target


def http_2xx(url: str, timeout: float) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return 200 <= response.status < 300, f"HTTP {response.status}"
    except Exception as exc:  # no secrets; only class name
        return False, type(exc).__name__


def launchd_running(label: str, timeout: float) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["/bin/launchctl", "print", label],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return False, type(exc).__name__
    out = proc.stdout or ""
    return proc.returncode == 0 and "state = running" in out, f"launchctl rc={proc.returncode}"


def run_check(check: dict[str, Any]) -> tuple[bool, str]:
    check_type = check.get("type", "none")
    if check_type == "none":
        return True, "none"
    if check_type == "http_2xx":
        return http_2xx(str(check["url"]), float(check.get("timeout_seconds", 5)))
    if check_type == "launchd_running":
        return launchd_running(str(check["label"]), float(check.get("timeout_seconds", 10)))
    raise RemediationError(f"unsupported check type: {check_type}")


def execute_command(command: list[str], timeout: int) -> tuple[int, str]:
    cmd_path = REPO_ROOT / command[0]
    if not cmd_path.exists():
        raise RemediationError(f"command not found: {command[0]}")
    cmd = [str(cmd_path), *[str(arg) for arg in command[1:]]]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = "\n".join(sanitize(line) for line in (proc.stdout or "").splitlines())
    return proc.returncode, output[-4000:]


def build_decision(args: argparse.Namespace, data: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    validate_rule(data, rule)
    if not condition_matches(rule, args.event, args.target):
        raise RemediationError("rule conditions do not match supplied event/target")

    env_name = data.get("require_global_enable_env", "BIF669_WATCHDOG_REMEDIATION_ENABLE")
    gates = {
        "execute_flag": bool(args.execute),
        "global_enable_env": os.environ.get(env_name) == "1",
        "allow_rule_flag": rule["id"] in set(args.allow_rule),
        "production_allowed": (not rule.get("production")) or bool(args.allow_production),
    }
    mode = "execute" if all(gates.values()) else "dry_run"
    return {"mode": mode, "gates": gates, "env_gate": env_name}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BIF-669 safe watchdog remediation gate")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    parser.add_argument("--rule", required=True)
    parser.add_argument("--event", required=True, choices=["down", "degraded"])
    parser.add_argument("--target", required=True)
    parser.add_argument("--execute", action="store_true", help="request execution; still gated by env and allow flags")
    parser.add_argument("--allow-rule", action="append", default=[], help="explicitly allow one rule id; repeatable")
    parser.add_argument("--allow-production", action="store_true", help="allow production-impacting rules when all other gates pass")
    parser.add_argument("--skip-precheck", action="store_true", help="test-only: do not perform precheck before command")
    parser.add_argument("--command-timeout", type=int, default=120)
    args = parser.parse_args(argv)

    try:
        data = load_rules(args.rules)
        rule = find_rule(data, args.rule)
        decision = build_decision(args, data, rule)

        result: dict[str, Any] = {
            "rule": rule["id"],
            "target": args.target,
            "event": args.event,
            "mode": decision["mode"],
            "gates": decision["gates"],
            "action_class": rule.get("action_class"),
            "risk": rule.get("risk"),
            "production": bool(rule.get("production")),
            "runbook": rule.get("runbook"),
        }

        if decision["mode"] != "execute":
            result["planned_command"] = rule.get("command")
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

        if not args.skip_precheck:
            ok, detail = run_check(rule.get("precheck", {"type": "none"}))
            result["precheck"] = {"ok": ok, "detail": detail}
            if ok:
                result["mode"] = "no_action_precheck_healthy"
                print(json.dumps(result, indent=2, sort_keys=True))
                return 0

        rc, output = execute_command(rule["command"], args.command_timeout)
        result["command_rc"] = rc
        if output:
            result["command_output_tail"] = output
        if rc != 0:
            result["mode"] = "failed"
            print(json.dumps(result, indent=2, sort_keys=True))
            return 2

        ok, detail = run_check(rule.get("postcheck", {"type": "none"}))
        result["postcheck"] = {"ok": ok, "detail": detail}
        result["mode"] = "remediated" if ok else "failed_postcheck"
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if ok else 3
    except RemediationError as exc:
        print(json.dumps({"error": sanitize(str(exc)), "mode": "blocked"}, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
