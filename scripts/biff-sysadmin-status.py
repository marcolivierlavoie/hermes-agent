#!/usr/bin/env python3
"""Read-only BIF-669 sysadmin access/readiness status probe.

This script is intentionally non-mutating: it verifies the access surfaces,
credential aliases, service reachability, and remediation-rule gates that make
Biff a practical remote sysadmin. It never prints credential values.
"""
from __future__ import annotations

import argparse
import json
import socket
import ssl
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def resolve_credential_helper() -> Path:
    """Resolve Marco's credential helper even under Hermes profile HOMEs."""
    candidates = [
        Path.home() / ".local/bin/get_credential.sh",
        Path("/Users/marco/.local/bin/get_credential.sh"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


CREDENTIAL_HELPER = resolve_credential_helper()
WATCHDOG_RUNNER = REPO_ROOT / "scripts/watchdog-remediate.py"

DEFAULT_CREDENTIAL_ALIASES = [
    "linear_api_key",
    "ha_token",
    "homeassistant",
    "homeassistant_token",
    "n8n_api_key",
    "unifi_username",
    "unifi_password",
    "nas_username",
    "nas_password",
    "kuma_username",
    "kuma_password",
]

DEFAULT_HTTP_SERVICES = {
    "biff_dashboard_tailnet": "https://biff.tail460c2.ts.net/",
    "n8n_health_tailnet": "https://biff.tail460c2.ts.net:5678/healthz",
    "proxmox_ui_tailnet": "https://biff.tail460c2.ts.net:8006/",
    "unifi_ui_tailnet": "https://biff.tail460c2.ts.net:8443/",
    "node_red_tailnet": "https://biff.tail460c2.ts.net:1880/",
    "immich_tailnet": "https://biff.tail460c2.ts.net:2283/",
    "adguard_tailnet": "https://biff.tail460c2.ts.net:3000/",
    "uptime_kuma_tailnet": "https://biff.tail460c2.ts.net:3001/",
    "nas_dsm_tailnet": "https://biff.tail460c2.ts.net:5001/",
}

DEFAULT_TCP_TARGETS = {
    "proxmox_ssh_lan": ("192.168.1.248", 22),
    "nas_smb_lan": ("Marco-NAS.local", 445),
    "slzb_zigbee_lan": ("192.168.1.201", 80),
}

DRY_RUN_RULES = [
    ("hermes_dashboard_down", "down", "hermes_dashboard"),
    ("hermes_gateway_down", "down", "hermes_gateway"),
    ("n8n_production_down", "down", "n8n_production"),
    ("uptimekuma_ct_down", "down", "uptimekuma_ct_130"),
    ("adguard_dns_down", "down", "adguard_dns_ct_101"),
]


def run_cmd(cmd: list[str], timeout: float = 10) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return False, type(exc).__name__
    text = (proc.stdout or "").strip()
    return proc.returncode == 0, text[-1200:]


def credential_status(alias: str) -> dict[str, Any]:
    if not CREDENTIAL_HELPER.exists():
        return {"ok": False, "source": "missing-helper", "detail": str(CREDENTIAL_HELPER)}
    ok, out = run_cmd(["bash", str(CREDENTIAL_HELPER), "--check", alias], timeout=15)
    source = "unknown"
    for part in out.split():
        if part.startswith("source="):
            source = part.split("=", 1)[1]
    available = ok and "available" in out
    detail = f"alias available source={source}" if available else "alias unavailable"
    return {"ok": available, "source": source, "detail": detail}


def http_status(url: str, timeout: float = 8) -> dict[str, Any]:
    ctx = ssl._create_unverified_context()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
            ok = 200 <= response.status < 400
            return {"ok": ok, "status": response.status, "url": response.geturl()}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def tcp_status(host: str, port: int, timeout: float = 5) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "host": host, "port": port}
    except Exception as exc:
        return {"ok": False, "host": host, "port": port, "error": type(exc).__name__}


def proxmox_guest_status() -> dict[str, Any]:
    ok, out = run_cmd(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "root@192.168.1.248",
            "hostname; qm list; pct list; pvesm status",
        ],
        timeout=25,
    )
    return {"ok": ok, "detail": out}


def remediation_dry_run(rule: str, event: str, target: str) -> dict[str, Any]:
    ok, out = run_cmd(
        [
            sys.executable,
            str(WATCHDOG_RUNNER),
            "--rule",
            rule,
            "--event",
            event,
            "--target",
            target,
        ],
        timeout=20,
    )
    parsed: dict[str, Any]
    try:
        parsed = json.loads(out)
    except Exception:
        parsed = {"raw": out}
    return {"ok": ok and parsed.get("mode") == "dry_run", "result": parsed}


def summarize(checks: dict[str, Any]) -> dict[str, int]:
    total = 0
    ok_count = 0

    def visit(value: Any) -> None:
        nonlocal total, ok_count
        if isinstance(value, dict):
            if "ok" in value:
                total += 1
                ok_count += 1 if value.get("ok") else 0
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(checks)
    return {"checks": total, "ok": ok_count, "failed": total - ok_count}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only BIF-669 sysadmin status probe")
    parser.add_argument("--skip-live", action="store_true", help="only validate local helpers/gates")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    args = parser.parse_args(argv)

    checks: dict[str, Any] = {
        "credential_aliases": {alias: credential_status(alias) for alias in DEFAULT_CREDENTIAL_ALIASES},
        "remediation_dry_runs": {
            rule: remediation_dry_run(rule, event, target) for rule, event, target in DRY_RUN_RULES
        },
    }

    if not args.skip_live:
        checks["http_services"] = {name: http_status(url) for name, url in DEFAULT_HTTP_SERVICES.items()}
        checks["tcp_targets"] = {
            name: tcp_status(host, port) for name, (host, port) in DEFAULT_TCP_TARGETS.items()
        }
        checks["proxmox"] = proxmox_guest_status()
        checks["tailscale_self"] = {"ok": run_cmd(["tailscale", "status", "--self"], timeout=10)[0]}
        checks["tailscale_serve"] = {"ok": run_cmd(["tailscale", "serve", "status"], timeout=10)[0]}

    report = {"schema_version": 1, "purpose": "BIF-669 read-only sysadmin status", "summary": summarize(checks), "checks": checks}
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if report["summary"]["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
