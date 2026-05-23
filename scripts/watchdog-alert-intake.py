#!/usr/bin/env python3
"""Autonomous watchdog alert intake and incident dedupe for Biff OS.

This script accepts a watchdog down/recovered signal, classifies known services,
records one active remediation incident per service/event, and suppresses repeat
stdout during cooldown so no-agent cron or Discord delivery paths do not spam.

It intentionally does not create Linear work items. Instead it writes stable
linear_dedupe_key and discord_dedupe_key fields that any later notifier/work-item
adapter must reuse.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path("~/.hermes/state/watchdog_alert_intake.json").expanduser()
DEFAULT_COOLDOWN_SECONDS = 1800

SERVICE_CATALOG: dict[str, dict[str, str]] = {
    "n8n_production": {
        "service": "n8n_production",
        "rule": "n8n_production_down",
        "severity": "critical",
        "failure": "n8n API unreachable",
    },
    "uptimekuma_ct_130": {
        "service": "uptimekuma_ct_130",
        "rule": "uptimekuma_ct_down",
        "severity": "critical",
        "failure": "Uptime Kuma CT130 unreachable",
    },
    "hermes_dashboard": {
        "service": "hermes_dashboard",
        "rule": "hermes_dashboard_down",
        "severity": "high",
        "failure": "Hermes dashboard unreachable",
    },
    "hermes_gateway": {
        "service": "hermes_gateway",
        "rule": "hermes_gateway_down",
        "severity": "high",
        "failure": "Hermes gateway down",
    },
    "proxmox": {
        "service": "proxmox",
        "rule": "manual_runbook_required",
        "severity": "critical",
        "failure": "Proxmox core degraded",
    },
    "homeassistant_zigbee_slzb": {
        "service": "homeassistant_zigbee_slzb",
        "rule": "manual_runbook_required",
        "severity": "high",
        "failure": "Home Assistant/Zigbee path degraded",
    },
    "adguard_dns_ct_101": {
        "service": "adguard_dns_ct_101",
        "rule": "adguard_dns_down",
        "severity": "critical",
        "failure": "AdGuard DNS CT101 unreachable",
    },
}

SOURCE_ALIASES: dict[str, str] = {
    "n8n_failure_watchdog.py": "n8n_production",
    "n8n failure watchdog": "n8n_production",
    "uptime_kuma_ct130_watchdog.py": "uptimekuma_ct_130",
    "uptime-kuma-ct130-watchdog.py": "uptimekuma_ct_130",
    "BIF-669 Uptime Kuma CT130 watchdog": "uptimekuma_ct_130",
    "proxmox_core_watchdog.py": "proxmox",
    "Proxmox/HA core watchdog": "proxmox",
}


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {"active_incidents": {}, "resolved_incidents": []}
    except Exception:
        return {"active_incidents": {}, "resolved_incidents": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def normalize_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def detect_event(message: str) -> str | None:
    lower = message.lower()
    if any(marker in lower for marker in ("recovered", "reachable again", "restored", "back up")):
        return "recovered"
    if any(marker in lower for marker in ("down", "unreachable", "timed out", "timeout", "connection refused", "degraded", "still_degraded")):
        return "down"
    return None


def classify_service(source: str, message: str, explicit_service: str | None = None) -> str | None:
    if explicit_service in SERVICE_CATALOG:
        return explicit_service

    source_clean = source.strip()
    if source_clean in SOURCE_ALIASES:
        return SOURCE_ALIASES[source_clean]

    text = f"{source} {message}".lower()
    if "n8n" in text:
        return "n8n_production"
    if "uptime kuma" in text or "ct130" in text or "ct 130" in text or ":3001" in text:
        return "uptimekuma_ct_130"
    if "hermes dashboard" in text or ":9119" in text:
        return "hermes_dashboard"
    if "hermes gateway" in text or "ai.hermes.gateway" in text:
        return "hermes_gateway"
    if "adguard" in text or "ct101" in text or "ct 101" in text or ":3000" in text:
        return "adguard_dns_ct_101"
    if "home assistant" in text or "ha core" in text or "zigbee" in text or "slzb" in text:
        return "homeassistant_zigbee_slzb"
    if "proxmox" in text or "pve_" in text or "e1000e" in text:
        return "proxmox"
    return None


def classify_alert(alert: dict[str, Any]) -> dict[str, Any] | None:
    source = normalize_text(alert.get("source") or alert.get("alert_source"))
    message = normalize_text(alert.get("message") or alert.get("text") or alert.get("body"))
    event = normalize_text(alert.get("event")) or detect_event(message)
    service = classify_service(source, message, normalize_text(alert.get("service") or alert.get("target")) or None)
    if event not in {"down", "recovered"} or not service:
        return None

    catalog = SERVICE_CATALOG[service]
    return {
        "alert_source": source or "unknown",
        "service": service,
        "event": event,
        "severity": catalog["severity"] if event == "down" else "info",
        "rule": catalog["rule"],
        "detected_failure": normalize_text(alert.get("detected_failure")) or catalog["failure"],
        "message": message,
    }


def incident_key(service: str, event: str = "down") -> str:
    return f"watchdog:{service}:{event}"


def build_record(classification: dict[str, Any], now_dt: datetime, cooldown_seconds: int, existing: dict[str, Any] | None) -> dict[str, Any]:
    key = incident_key(classification["service"], "down")
    timestamp = iso(now_dt)
    cooldown_until = iso(now_dt + timedelta(seconds=cooldown_seconds))
    if existing:
        first_seen = existing.get("first_seen") or timestamp
        attempt_count = int(existing.get("attempt_count") or 0) + 1
        alert_count = int(existing.get("alert_count") or 0) + 1
    else:
        first_seen = timestamp
        attempt_count = 1
        alert_count = 1

    rule = classification["rule"]
    if rule == "manual_runbook_required":
        next_step = f"open runbook and classify bounded remediation for {classification['service']}"
    else:
        next_step = f"run gated remediation rule {rule}"

    return {
        "incident_key": key,
        "alert_source": classification["alert_source"],
        "service": classification["service"],
        "event": "down",
        "severity": classification["severity"],
        "timestamp": timestamp,
        "first_seen": first_seen,
        "last_seen": timestamp,
        "detected_failure": classification["detected_failure"],
        "current_next_step": next_step,
        "remediation_rule": rule,
        "attempt_count": attempt_count,
        "alert_count": alert_count,
        "last_attempt_at": timestamp,
        "cooldown_until": cooldown_until,
        "status": "active",
        "linear_dedupe_key": key,
        "discord_dedupe_key": key,
    }


def handle_alert(alert: dict[str, Any], state: dict[str, Any], now_dt: datetime, cooldown_seconds: int) -> dict[str, Any] | None:
    classification = classify_alert(alert)
    if not classification:
        return None

    state.setdefault("active_incidents", {})
    state.setdefault("resolved_incidents", [])
    key = incident_key(classification["service"], "down")

    if classification["event"] == "recovered":
        existing = state["active_incidents"].pop(key, None)
        if existing:
            existing["status"] = "resolved"
            existing["resolved_at"] = iso(now_dt)
            existing["last_seen"] = iso(now_dt)
            state["resolved_incidents"].insert(0, existing)
            state["resolved_incidents"] = state["resolved_incidents"][:50]
        return {"action": "incident_resolved", "incident": existing, "spam_suppressed": True} if existing else None

    existing = state["active_incidents"].get(key)
    if existing:
        cooldown_until = parse_time(existing.get("cooldown_until"))
        if now_dt < cooldown_until:
            existing["last_seen"] = iso(now_dt)
            existing["timestamp"] = iso(now_dt)
            existing["alert_count"] = int(existing.get("alert_count") or 0) + 1
            existing["last_suppressed_reason"] = "cooldown"
            state["active_incidents"][key] = existing
            return {"action": "deduped_cooldown", "incident": existing, "spam_suppressed": True}

    record = build_record(classification, now_dt, cooldown_seconds, existing)
    state["active_incidents"][key] = record
    return {
        "action": "remediation_record_updated" if existing else "remediation_record_created",
        "incident": record,
        "spam_suppressed": False,
    }


def load_alert(args: argparse.Namespace) -> dict[str, Any]:
    if args.alert_json:
        data = json.loads(args.alert_json)
    else:
        stdin = sys.stdin.read().strip()
        data = json.loads(stdin) if stdin.startswith("{") else {"message": stdin}
    if args.source:
        data["source"] = args.source
    if args.message:
        data["message"] = args.message
    if args.service:
        data["service"] = args.service
    if args.event:
        data["event"] = args.event
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Biff watchdog alert intake/dedupe")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--alert-json", help="JSON alert with source/message/timestamp fields; defaults to stdin")
    parser.add_argument("--source")
    parser.add_argument("--message")
    parser.add_argument("--service")
    parser.add_argument("--event", choices=["down", "recovered"])
    parser.add_argument("--now", help="ISO timestamp override for deterministic tests")
    parser.add_argument("--cooldown-seconds", type=int, default=DEFAULT_COOLDOWN_SECONDS)
    parser.add_argument("--print-noop", action="store_true", help="print dedupe/resolved/noop actions for debugging; default is silent")
    args = parser.parse_args(argv)

    alert = load_alert(args)
    now_dt = parse_time(args.now or normalize_text(alert.get("timestamp")) or None)
    state = load_state(args.state)
    outcome = handle_alert(alert, state, now_dt, args.cooldown_seconds)
    if outcome is not None:
        save_state(args.state, state)

    if not outcome:
        return 0
    if outcome.get("spam_suppressed") and not args.print_noop:
        return 0
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
