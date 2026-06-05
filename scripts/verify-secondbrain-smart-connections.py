#!/usr/bin/env python3
"""Compact SecondBrain Smart Connections/index status verifier.

The verifier prints a bounded JSON verdict: PASS/WARN/FAIL, checked components,
evidence paths, and exact follow-up.  It is fixture-friendly via CLI flags and
never dumps note contents, database rows, API keys, or large raw payloads.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


def _path(raw: str | None) -> Path | None:
    if not raw:
        return None
    return Path(raw).expanduser()


def _safe_stat(path: Path | None) -> dict[str, Any]:
    if not path:
        return {"path": None, "exists": False}
    try:
        st = path.stat()
        return {"path": str(path), "exists": True, "bytes": st.st_size, "mtime": int(st.st_mtime)}
    except FileNotFoundError:
        return {"path": str(path), "exists": False}


def _sqlite_summary(path: Path | None) -> dict[str, Any]:
    info = _safe_stat(path)
    if not info.get("exists"):
        return info
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 20")]
            counts: dict[str, int | str] = {}
            for table in tables[:8]:
                if any(token in table.lower() for token in ("embedding", "vector", "note", "file", "chunk", "document", "item")):
                    try:
                        counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                    except Exception as exc:
                        counts[table] = f"count_error:{type(exc).__name__}"
            info.update({"readable": True, "tables": tables, "counts": counts})
        finally:
            conn.close()
    except Exception as exc:
        info.update({"readable": False, "error": type(exc).__name__})
    return info


def _find_existing(candidates: list[Path]) -> Path | None:
    for p in candidates:
        if p.exists():
            return p
    return None


def verify_secondbrain(
    *,
    vault: Path | None = None,
    smart_connections_dir: Path | None = None,
    sqlite_db: Path | None = None,
    schedule: Path | None = None,
    max_age_hours: int = 48,
) -> dict[str, Any]:
    home = Path.home()
    vault = vault or _path(os.getenv("SECOND_BRAIN_VAULT")) or home / "Obsidian" / "SecondBrain"
    sc_dir = smart_connections_dir or _path(os.getenv("SMART_CONNECTIONS_DIR")) or vault / ".obsidian" / "plugins" / "smart-connections"
    sqlite_db = sqlite_db or _path(os.getenv("SECOND_BRAIN_SQLITE_DB")) or _find_existing([
        vault / ".smart-connections" / "smart-connections.db",
        sc_dir / "smart-connections.db",
        sc_dir / "data.db",
        vault / "SecondBrain.sqlite",
    ])
    schedule = schedule or _path(os.getenv("SECOND_BRAIN_REFRESH_SCHEDULE")) or _find_existing([
        home / ".hermes" / "cron" / "secondbrain-smart-connections-refresh.json",
        home / "Library" / "LaunchAgents" / "com.marco.secondbrain.smart-connections-refresh.plist",
    ])

    checks: list[dict[str, Any]] = []
    checks.append({"component": "vault", **_safe_stat(vault)})
    checks.append({"component": "smart_connections_plugin_dir", **_safe_stat(sc_dir)})
    checks.append({"component": "sqlite_index", **_sqlite_summary(sqlite_db)})
    checks.append({"component": "refresh_schedule", **_safe_stat(schedule)})

    now = int(time.time())
    verdict = "PASS"
    followups: list[str] = []
    by_component = {c["component"]: c for c in checks}
    if not by_component["vault"].get("exists"):
        verdict = "FAIL"
        followups.append("Set SECOND_BRAIN_VAULT or pass --vault to the Obsidian SecondBrain vault path.")
    if not by_component["smart_connections_plugin_dir"].get("exists"):
        verdict = "FAIL"
        followups.append("Install/enable Smart Connections or pass --smart-connections-dir to its plugin data directory.")
    index = by_component["sqlite_index"]
    if not index.get("exists"):
        verdict = "WARN" if verdict == "PASS" else verdict
        followups.append("Run the Smart Connections/index refresh and pass --sqlite-db if the DB lives at a nonstandard path.")
    elif not index.get("readable", True):
        verdict = "FAIL"
        followups.append("Repair or regenerate the SQLite index; read-only open failed.")
    elif index.get("mtime") and now - int(index["mtime"]) > max_age_hours * 3600:
        verdict = "WARN" if verdict == "PASS" else verdict
        followups.append(f"Refresh the Smart Connections/index DB; index is older than {max_age_hours}h.")
    if not by_component["refresh_schedule"].get("exists"):
        verdict = "WARN" if verdict == "PASS" else verdict
        followups.append("Add/repair the scheduled refresh job; no cron/LaunchAgent evidence was found.")

    return {
        "verdict": verdict,
        "checked_at_epoch": now,
        "components": checks,
        "evidence_paths": [c.get("path") for c in checks if c.get("path")],
        "follow_up": followups or ["No follow-up required based on checked evidence."],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vault")
    ap.add_argument("--smart-connections-dir")
    ap.add_argument("--sqlite-db")
    ap.add_argument("--schedule")
    ap.add_argument("--max-age-hours", type=int, default=48)
    args = ap.parse_args(argv)
    result = verify_secondbrain(
        vault=_path(args.vault),
        smart_connections_dir=_path(args.smart_connections_dir),
        sqlite_db=_path(args.sqlite_db),
        schedule=_path(args.schedule),
        max_age_hours=max(1, args.max_age_hours),
    )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result["verdict"] == "PASS" else 1 if result["verdict"] == "WARN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
