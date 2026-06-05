#!/usr/bin/env python3
"""Read-only status primitive for BIF-669 core homelab systems.

This script is deliberately non-mutating: it performs fixed, hardcoded status
checks only. It does not restart HA, Zigbee/SLZB, DNS, Immich/Postgres,
Node-RED, NAS/DSM, UniFi, Proxmox guests, or hosts. It never prints credential
values.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
SECRET_MARKERS = ("token", "password", "passwd", "secret", "api_key", "apikey", "authorization", "bearer")


@dataclass(frozen=True)
class HttpCheck:
    name: str
    url: str
    ok_status_min: int = 200
    ok_status_max: int = 399


@dataclass(frozen=True)
class TcpCheck:
    name: str
    host: str
    port: int


SYSTEMS: dict[str, dict[str, Any]] = {
    "homeassistant_zigbee_slzb": {
        "description": "Home Assistant, Zigbee2MQTT surface, and SLZB coordinator UI reachability.",
        "http": [
            HttpCheck("homeassistant_tailnet", "http://homeassistant.tail460c2.ts.net:8123/"),
            HttpCheck("homeassistant_lan", "http://192.168.1.161:8123/"),
            HttpCheck("slzb_lan", "http://192.168.1.201/"),
        ],
        "tcp": [TcpCheck("slzb_http_tcp", "192.168.1.201", 80)],
        "proxmox": ["qm status 100"],
        "credential_aliases": ["ha_token", "homeassistant", "homeassistant_token"],
        "ha_api": True,
    },
    "adguard_dns": {
        "description": "AdGuard DNS CT 101 status and resolver smoke.",
        "http": [HttpCheck("adguard_tailnet", "https://biff.tail460c2.ts.net:3000/")],
        "tcp": [TcpCheck("adguard_dns_tcp", "192.168.1.162", 53)],
        "proxmox": ["pct status 101"],
        "dns": {"server": "192.168.1.162", "name": "example.com"},
    },
    "immich_postgres": {
        "description": "Immich CT 102 and Postgres CT 131 read-only status.",
        "http": [HttpCheck("immich_tailnet", "https://biff.tail460c2.ts.net:2283/")],
        "tcp": [TcpCheck("immich_http_tcp", "192.168.1.229", 2283)],
        "proxmox": ["pct status 102", "pct status 131"],
    },
    "node_red": {
        "description": "Node-RED CT 103 read-only status.",
        "http": [HttpCheck("node_red_tailnet", "https://biff.tail460c2.ts.net:1880/")],
        "tcp": [TcpCheck("node_red_http_tcp", "192.168.1.147", 1880)],
        "proxmox": ["pct status 103"],
    },
    "nas_dsm": {
        "description": "Synology DSM/Tailscale status and Proxmox NAS-CIFS storage dependency.",
        "http": [
            HttpCheck("nas_dsm_tailnet_serve", "https://biff.tail460c2.ts.net:5001/"),
            HttpCheck("nas_dsm_magicdns", "https://marco-nas.tail460c2.ts.net:5001/"),
        ],
        "tcp": [TcpCheck("nas_smb_tcp", "192.168.1.62", 445)],
        "credential_aliases": ["nas_username", "nas_password"],
        "proxmox": ["pvesm status | grep NAS-CIFS"],
        "tailscale_ping": "marco-nas.tail460c2.ts.net",
    },
    "unifi": {
        "description": "UniFi/router admin surface reachability only; no network config changes.",
        "http": [HttpCheck("unifi_tailnet", "https://biff.tail460c2.ts.net:8443/")],
        "tcp": [TcpCheck("unifi_https_tcp", "192.168.1.1", 443)],
        "credential_aliases": ["unifi_username", "unifi_password"],
    },
}


def sanitize(value: str) -> str:
    lowered = value.lower()
    if any(marker in lowered for marker in SECRET_MARKERS):
        return "[redacted-sensitive-output]"
    return value


def run_cmd(cmd: list[str], timeout: float = 10, env: dict[str, str] | None = None) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except Exception as exc:
        return False, type(exc).__name__
    out = "\n".join(sanitize(line) for line in (proc.stdout or "").splitlines())
    return proc.returncode == 0, out[-1500:]


def http_status(check: HttpCheck, timeout: float = 8) -> dict[str, Any]:
    ctx = ssl._create_unverified_context()
    try:
        req = urllib.request.Request(check.url, method="GET")
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
            ok = check.ok_status_min <= response.status <= check.ok_status_max
            return {"ok": ok, "status": response.status, "url": response.geturl()}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def tcp_status(check: TcpCheck, timeout: float = 5) -> dict[str, Any]:
    try:
        with socket.create_connection((check.host, check.port), timeout=timeout):
            return {"ok": True, "host": check.host, "port": check.port}
    except Exception as exc:
        return {"ok": False, "host": check.host, "port": check.port, "error": type(exc).__name__}


def credential_check(alias: str) -> dict[str, Any]:
    if not CREDENTIAL_HELPER.exists():
        return {"ok": False, "source": "missing-helper"}
    try:
        proc = subprocess.run(
            ["bash", str(CREDENTIAL_HELPER), "--check", alias],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
            check=False,
        )
        ok = proc.returncode == 0
        out = proc.stdout or ""
    except Exception:
        ok = False
        out = ""
    source = "unknown"
    for part in out.split():
        if part.startswith("source="):
            source = part.split("=", 1)[1]
    available = ok and "available" in out
    detail = f"alias available source={source}" if available else "alias unavailable"
    return {"ok": available, "source": source, "detail": detail}


def get_credential(alias: str) -> str | None:
    if not CREDENTIAL_HELPER.exists():
        return None
    try:
        proc = subprocess.run(
            ["bash", str(CREDENTIAL_HELPER), alias],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def ha_api_status() -> dict[str, Any]:
    token = get_credential("ha_token") or get_credential("homeassistant") or get_credential("homeassistant_token")
    if not token:
        return {"ok": False, "error": "missing_credential_alias"}
    try:
        req = urllib.request.Request(
            "http://homeassistant.tail460c2.ts.net:8123/api/",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            return {"ok": 200 <= response.status < 300, "status": response.status}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def proxmox_status(command: str) -> dict[str, Any]:
    ok, out = run_cmd(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@192.168.1.248", command],
        timeout=20,
    )
    return {"ok": ok, "command": command, "detail": out}


def dns_status(server: str, name: str) -> dict[str, Any]:
    dig = shutil_which("dig")
    if dig:
        ok, out = run_cmd([dig, "+time=3", "+tries=1", f"@{server}", name, "A"], timeout=8)
        return {"ok": ok, "server": server, "name": name, "tool": "dig", "detail": out[-400:]}
    nslookup = shutil_which("nslookup")
    if nslookup:
        ok, out = run_cmd([nslookup, "-timeout=3", name, server], timeout=8)
        return {"ok": ok, "server": server, "name": name, "tool": "nslookup", "detail": out[-400:]}
    return {"ok": False, "server": server, "name": name, "error": "no_dig_or_nslookup"}


def shutil_which(name: str) -> str | None:
    for path in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(path) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def tailscale_ping(host: str) -> dict[str, Any]:
    ok, out = run_cmd(["tailscale", "ping", "--timeout=5s", "--c=1", host], timeout=10)
    return {"ok": ok, "host": host, "detail": out}


def collect_system(system_id: str, *, skip_live: bool = False) -> dict[str, Any]:
    spec = SYSTEMS[system_id]
    result: dict[str, Any] = {"ok": True, "description": spec["description"], "mutates": False}

    if spec.get("credential_aliases"):
        result["credential_aliases"] = {alias: credential_check(alias) for alias in spec["credential_aliases"]}

    if skip_live:
        result["live_checks_skipped"] = True
    else:
        result["http"] = {check.name: http_status(check) for check in spec.get("http", [])}
        result["tcp"] = {check.name: tcp_status(check) for check in spec.get("tcp", [])}
        result["proxmox"] = {cmd: proxmox_status(cmd) for cmd in spec.get("proxmox", [])}
        if spec.get("ha_api"):
            result["ha_api"] = ha_api_status()
        if spec.get("dns"):
            dns = spec["dns"]
            result["dns"] = dns_status(dns["server"], dns["name"])
        if spec.get("tailscale_ping"):
            result["tailscale_ping"] = tailscale_ping(spec["tailscale_ping"])

    result["ok"] = summarize(result)["failed"] == 0
    return result


def summarize(value: Any) -> dict[str, int]:
    total = 0
    ok_count = 0

    def visit(child: Any) -> None:
        nonlocal total, ok_count
        if isinstance(child, dict):
            if "ok" in child and child.get("mutates") is not False:
                total += 1
                ok_count += 1 if child.get("ok") else 0
            for nested in child.values():
                visit(nested)
        elif isinstance(child, list):
            for nested in child:
                visit(nested)

    visit(value)
    return {"checks": total, "ok": ok_count, "failed": total - ok_count}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BIF-669 read-only core system status primitive")
    parser.add_argument("--system", action="append", choices=sorted(SYSTEMS), help="limit to one system; repeatable")
    parser.add_argument("--skip-live", action="store_true", help="only validate local definitions and credential alias checks")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    selected = args.system or sorted(SYSTEMS)
    systems = {system_id: collect_system(system_id, skip_live=args.skip_live) for system_id in selected}
    report = {
        "schema_version": 1,
        "purpose": "BIF-669 read-only hardcoded status primitive for core homelab systems",
        "mutates": False,
        "systems": systems,
        "summary": summarize(systems),
    }
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if report["summary"]["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
