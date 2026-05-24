#!/usr/bin/env python3
"""One-shot verifier for SecondBrain Smart Connections/SQLite/index-refresh status.

The verifier is intentionally read-only and compact: it reports PASS/WARN/FAIL,
checked components, evidence paths, and exact follow-up without dumping note text
or secret-bearing environment data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

DEFAULT_VAULT_CANDIDATES = [
    Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/SecondBrain",
    Path.home() / "Documents/SecondBrain",
    Path.home() / "docs/SecondBrain",
]


def _find_vault(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    for p in DEFAULT_VAULT_CANDIDATES:
        if p.exists():
            return p
    return None


def _find_sqlite_files(vault: Path, explicit: str | None) -> list[Path]:
    if explicit:
        p = Path(explicit).expanduser()
        return [p] if p.exists() else []
    patterns = ["**/*.sqlite", "**/*.sqlite3", "**/*.db"]
    out: list[Path] = []
    roots = [vault / ".smart-connections", vault / ".obsidian", vault]
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            out.extend(p for p in root.glob(pattern) if p.is_file())
        if out:
            break
    return sorted(set(out), key=lambda p: p.stat().st_mtime, reverse=True)[:8]


def _sqlite_probe(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(path), "exists": path.exists(), "size_bytes": path.stat().st_size if path.exists() else 0}
    if not path.exists() or path.stat().st_size == 0:
        info["ok"] = False
        info["error"] = "missing_or_empty"
        return info
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
            counts = {}
            for table in tables[:20]:
                safe = table.replace('"', '""')
                try:
                    counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{safe}"').fetchone()[0])
                except Exception:
                    pass
            info.update({"ok": True, "tables": tables[:20], "counts": counts})
        finally:
            conn.close()
    except Exception as exc:
        info.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return info


def verify(vault: str | None = None, sqlite_path: str | None = None, schedule_path: str | None = None) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    follow_up: list[str] = []
    status = "PASS"
    vault_path = _find_vault(vault)
    if not vault_path:
        return {"status": "FAIL", "checked": [], "evidence_paths": [], "follow_up": ["Configure or pass --vault for the SecondBrain vault path."]}
    checked.append({"component": "vault", "status": "PASS", "path": str(vault_path)})
    sqlite_files = _find_sqlite_files(vault_path, sqlite_path)
    if not sqlite_files:
        status = "FAIL"
        follow_up.append("Run/verify Smart Connections indexing; no SQLite/db file was found under the vault.")
    probes = [_sqlite_probe(p) for p in sqlite_files]
    for probe in probes:
        component_status = "PASS" if probe.get("ok") and probe.get("size_bytes", 0) > 0 else "FAIL"
        checked.append({"component": "sqlite", "status": component_status, "path": probe.get("path"), "size_bytes": probe.get("size_bytes"), "tables": probe.get("tables", [])[:8]})
        if component_status == "FAIL":
            status = "FAIL"
            follow_up.append(f"Repair or refresh SQLite index: {probe.get('path')} ({probe.get('error')})")
    if probes and not any((probe.get("counts") or {}) for probe in probes if probe.get("ok")):
        status = "WARN" if status == "PASS" else status
        follow_up.append("SQLite opens, but no table counts were readable; inspect Smart Connections schema/index freshness.")
    schedule = Path(schedule_path).expanduser() if schedule_path else vault_path / ".smart-connections" / "last_refresh.json"
    if schedule.exists():
        checked.append({"component": "refresh_schedule", "status": "PASS", "path": str(schedule), "mtime": int(schedule.stat().st_mtime)})
    else:
        if status == "PASS":
            status = "WARN"
        checked.append({"component": "refresh_schedule", "status": "WARN", "path": str(schedule)})
        follow_up.append("Add or verify a scheduled/index-refresh marker; schedule evidence is missing.")
    evidence_paths = [c["path"] for c in checked if c.get("path")]
    return {"status": status, "checked": checked, "evidence_paths": evidence_paths, "follow_up": follow_up or ["No follow-up required."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault")
    parser.add_argument("--sqlite")
    parser.add_argument("--schedule")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    result = verify(args.vault, args.sqlite, args.schedule)
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["status"])
        print("checked: " + ", ".join(f"{c['component']}={c['status']}" for c in result["checked"]))
        print("evidence: " + ", ".join(result["evidence_paths"][:8]))
        print("follow_up: " + " | ".join(result["follow_up"]))
    return 0 if result["status"] in {"PASS", "WARN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
