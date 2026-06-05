"""Read-only Security/Trust posture collectors for the dashboard.

This module intentionally uses bounded, local-only collectors. It does not
read credential values, expose secret paths, scan arbitrary hosts, or mutate
system state.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from hermes_cli import __version__
from hermes_cli.config import DEFAULT_CONFIG, OPTIONAL_ENV_VARS, get_env_path, load_config

DEFAULT_ALLOWED_PUBLIC_PORTS = {"22", "80", "443"}
LISTENER_TIMEOUT_SECONDS = 2.5
VERSION_TIMEOUT_SECONDS = 2.0

SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|credential|oauth|auth)", re.I)
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "[::1]"}
ALL_INTERFACE_HOSTS = {"*", "0.0.0.0", "::", "[::]"}


def _run(cmd: list[str], timeout: float = VERSION_TIMEOUT_SECONDS) -> tuple[int, str]:
    """Run a local read-only command without a shell."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, (proc.stdout or "").strip()
    except FileNotFoundError:
        return 127, "not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as exc:  # pragma: no cover - defensive: endpoint must degrade
        return 1, f"unavailable: {type(exc).__name__}"


def _first_line(text: str) -> str:
    return text.splitlines()[0].strip() if text else "unavailable"


def _mask_address(address: str) -> str:
    host, sep, port = address.rpartition(":")
    if not sep:
        return "masked"
    clean_host = host.strip("[]")
    if clean_host in LOOPBACK_HOSTS:
        masked = "127.0.0.x" if ":" not in clean_host else "::1"
    elif clean_host in ALL_INTERFACE_HOSTS:
        masked = "all-interfaces"
    elif re.match(r"^\d+\.\d+\.\d+\.\d+$", clean_host):
        parts = clean_host.split(".")
        masked = f"{parts[0]}.{parts[1]}.x.x"
    elif ":" in clean_host:
        masked = "ipv6-masked"
    else:
        masked = "host-masked"
    return f"{masked}:{port}"


def _parse_lsof_listeners(output: str) -> list[dict[str, Any]]:
    listeners: list[dict[str, Any]] = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        name = parts[0]
        pid = parts[1]
        address = parts[-2] if parts[-1] == "(LISTEN)" else parts[-1]
        if "->" in address:
            continue
        host, sep, port = address.rpartition(":")
        if not sep or not port:
            continue
        host_clean = host.strip("[]") or "*"
        scope = "public" if host_clean in ALL_INTERFACE_HOSTS else "loopback" if host_clean in LOOPBACK_HOSTS else "interface"
        listeners.append(
            {
                "process": name,
                "pid": pid if pid.isdigit() else None,
                "port": port,
                "scope": scope,
                "address": _mask_address(address),
            }
        )
    return listeners


def _collect_listeners() -> dict[str, Any]:
    if platform.system() == "Darwin" and shutil.which("lsof"):
        code, output = _run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], timeout=LISTENER_TIMEOUT_SECONDS)
        return {
            "collector": "lsof -nP -iTCP -sTCP:LISTEN",
            "available": code == 0,
            "error": None if code == 0 else _first_line(output),
            "listeners": _parse_lsof_listeners(output) if code == 0 else [],
        }
    if shutil.which("ss"):
        code, output = _run(["ss", "-ltnp"], timeout=LISTENER_TIMEOUT_SECONDS)
        # Keep Linux fallback compact and non-fatal; detailed parser can be extended later.
        listeners = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            address = parts[3]
            host, sep, port = address.rpartition(":")
            if not sep:
                continue
            host_clean = host.strip("[]") or "*"
            scope = "public" if host_clean in ALL_INTERFACE_HOSTS else "loopback" if host_clean in LOOPBACK_HOSTS else "interface"
            listeners.append({"process": "unknown", "pid": None, "port": port, "scope": scope, "address": _mask_address(address)})
        return {"collector": "ss -ltnp", "available": code == 0, "error": None if code == 0 else _first_line(output), "listeners": listeners}
    return {"collector": "none", "available": False, "error": "No safe listener collector available", "listeners": []}


def _allowed_public_ports() -> set[str]:
    ports = set(DEFAULT_ALLOWED_PUBLIC_PORTS)
    raw = os.getenv("HERMES_SECURITY_ALLOWED_PUBLIC_PORTS", "")
    for value in raw.split(","):
        value = value.strip()
        if value.isdigit():
            ports.add(value)
    return ports


def _collect_versions() -> dict[str, str]:
    versions: dict[str, str] = {
        "hermes": __version__,
        "os": platform.platform(),
    }
    for key, cmd in {
        "node": ["node", "--version"],
        "npm": ["npm", "--version"],
        "python": ["python3", "--version"],
        "openssl": ["openssl", "version"],
    }.items():
        _, output = _run(cmd)
        versions[key] = _first_line(output)
    return versions


def _collect_package_updates() -> dict[str, Any]:
    if platform.system() == "Darwin":
        return {"apt": {"status": "not_applicable", "reason": "macOS host; apt drift is N/A"}}
    if not shutil.which("apt"):
        return {"apt": {"status": "not_available", "reason": "apt not installed"}}
    return {"apt": {"status": "not_collected", "reason": "read-only endpoint avoids package-manager mutation/cache changes"}}


def _collect_service_status() -> dict[str, Any]:
    system = platform.system()
    if system == "Darwin":
        code, output = _run(["launchctl", "print", "system"], timeout=2.0)
        return {"manager": "launchd", "available": code == 0, "summary": "launchd available" if code == 0 else _first_line(output)}
    if shutil.which("systemctl"):
        code, output = _run(["systemctl", "is-system-running"], timeout=2.0)
        return {"manager": "systemd", "available": code in {0, 1}, "summary": _first_line(output)}
    return {"manager": "unknown", "available": False, "summary": "No launchd/systemd collector available"}


def _flatten_config_keys(config: dict[str, Any], prefix: str = "") -> list[str]:
    keys: list[str] = []
    for key, value in config.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            keys.extend(_flatten_config_keys(value, full))
        else:
            keys.append(full)
    return keys


def _collect_compliance() -> dict[str, Any]:
    config = load_config()
    config_keys = _flatten_config_keys(config)
    secret_like_config_count = sum(1 for key in config_keys if SECRET_KEY_RE.search(key))
    env_file = get_env_path()
    env_key_count = 0
    known_secret_env_count = 0
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key = stripped.split("=", 1)[0].strip()
                env_key_count += 1
                if SECRET_KEY_RE.search(key) or key in OPTIONAL_ENV_VARS:
                    known_secret_env_count += 1
        except OSError:
            pass
    categories = sorted({key.split(".", 1)[0] for key in config_keys if key})
    return {
        "secret_paths_exposed": 0,
        "secret_values_exposed": 0,
        "env_key_count": env_key_count,
        "known_secret_env_key_count": known_secret_env_count,
        "secret_like_config_key_count": secret_like_config_count,
        "config_category_count": len(categories),
        "config_categories": categories[:24],
    }


def build_security_posture() -> dict[str, Any]:
    generated_at = time.time()
    listener_result = _collect_listeners()
    listeners = listener_result["listeners"]
    allowed_ports = _allowed_public_ports()
    public_listeners = [item for item in listeners if item["scope"] == "public"]
    unapproved_public = [item for item in public_listeners if str(item["port"]) not in allowed_ports]

    findings: list[dict[str, Any]] = []
    if unapproved_public:
        findings.append(
            {
                "id": "public-listeners-review",
                "severity": "medium",
                "title": "Review all-interface listeners",
                "detail": f"{len(unapproved_public)} listener(s) bind to all interfaces outside the allow-list.",
                "why": "All-interface binds can expose local services beyond loopback if network controls allow it.",
                "items": [f"{item['process']} on {item['address']} ({item['scope']})" for item in unapproved_public[:8]],
                "recommendations": ["Confirm each public bind is intentional", "Prefer loopback binding for local dashboards and developer services", "Add intentional ports to HERMES_SECURITY_ALLOWED_PUBLIC_PORTS"],
                "nextProbe": "Re-run /api/security after service bind changes or allow-list updates.",
            }
        )
    elif public_listeners:
        findings.append(
            {
                "id": "public-listeners-allowed",
                "severity": "low",
                "title": "All-interface listeners are allow-listed",
                "detail": f"{len(public_listeners)} public listener(s) found, all on allowed ports.",
                "why": "Known public services should remain explicitly reviewed even when allowed.",
                "items": [f"port {item['port']} ({item['address']})" for item in public_listeners[:8]],
                "recommendations": ["Keep the allow-list narrow", "Prefer reverse proxy or Tailscale controls for exposed services"],
                "nextProbe": "Check after adding services or changing network exposure.",
            }
        )

    if not listener_result["available"]:
        findings.append(
            {
                "id": "listener-collector-unavailable",
                "severity": "low",
                "title": "Listener collector unavailable",
                "detail": listener_result["error"] or "No listener data collected.",
                "why": "Network exposure confidence is lower without local listener data.",
                "items": [],
                "recommendations": ["Verify lsof is available on macOS or ss is available on Linux"],
                "nextProbe": "Run the endpoint again after installing/repairing the local collector.",
            }
        )

    compliance = _collect_compliance()
    versions = _collect_versions()
    package_updates = _collect_package_updates()
    service_status = _collect_service_status()

    medium_count = sum(1 for finding in findings if finding["severity"] in {"medium", "high", "critical"})
    score = max(0, 100 - (20 * len(unapproved_public)) - (10 if not listener_result["available"] else 0))
    label = "Review" if medium_count else "Good"
    summary = (
        "Review public listener exposure before widening access."
        if medium_count
        else "No unapproved public listeners found by safe local collectors."
    )

    exposure = {
        "listeners_total": len(listeners),
        "public_listeners": len(public_listeners),
        "unapproved_public_listeners": len(unapproved_public),
        "allowed_public_ports": sorted(allowed_ports, key=lambda x: int(x) if x.isdigit() else 99999),
        "listeners": listeners[:24],
    }
    network = {
        "collector": listener_result["collector"],
        "available": listener_result["available"],
        "masked_ips": True,
        "raw_paths_or_values": False,
        "error": listener_result["error"],
    }

    return {
        "score": score,
        "label": label,
        "summary": summary,
        "findings": findings,
        "compliance": compliance,
        "versions": versions,
        "packageUpdates": package_updates,
        "exposure": exposure,
        "network": {**network, "serviceStatus": service_status},
        "note": "Read-only local posture summary. IPs are masked; credential values and secret paths are never returned.",
        "generated_at": generated_at,
        "read_only": True,
    }
