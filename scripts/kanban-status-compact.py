#!/usr/bin/env python3
"""Compact direct-query-first Kanban status helper.

Given K-#### display IDs, query the Kanban SQLite source of truth directly and
print a bounded JSON verdict with task status, assignee, result, recent comments,
events, runs, and attention/blocking indicators.  This is intentionally shaped
before output reaches the model context so Discord status checks do not require
broad searches or large follow-up reads.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Any

from hermes_cli import kanban_db


def _trim(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 30)].rstrip() + f"...[trimmed {len(text) - limit} chars]"


def _rows(conn, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _find_task(conn, display_id: str) -> dict[str, Any] | None:
    rows = _rows(
        conn,
        """
        SELECT id, display_id, title, assignee, status, priority, tenant,
               created_at, started_at, completed_at, result, body
        FROM tasks
        WHERE display_id = ? OR id = ?
        LIMIT 1
        """,
        (display_id, display_id),
    )
    return rows[0] if rows else None


def compact_status(display_ids: list[str], *, board: str | None = None, limit: int = 5) -> dict[str, Any]:
    conn = kanban_db.connect(board=board)
    out: dict[str, Any] = {
        "ok": True,
        "board": board or kanban_db.resolve_board_slug(None),
        "checked_at_epoch": int(time.time()),
        "tasks": [],
    }
    try:
        for display_id in display_ids:
            task = _find_task(conn, display_id)
            if not task:
                out["ok"] = False
                out["tasks"].append({"display_id": display_id, "found": False, "attention": ["not_found"]})
                continue
            task_id = task["id"]
            comments = _rows(
                conn,
                "SELECT author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            )
            events = _rows(
                conn,
                "SELECT kind, payload, created_at FROM task_events WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            )
            runs = _rows(
                conn,
                "SELECT outcome, summary, error, started_at, ended_at FROM task_runs WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
                (task_id, limit),
            )
            attention: list[str] = []
            status = str(task.get("status") or "")
            if status in {"blocked", "triage", "running", "review"}:
                attention.append(status)
            if any("block" in str(c.get("body") or "").lower() for c in comments[:2]):
                attention.append("recent_blocking_comment")
            if runs and str(runs[0].get("outcome") or "") in {"failed", "crashed", "timed_out"}:
                attention.append(f"latest_run_{runs[0]['outcome']}")
            out["tasks"].append(
                {
                    "display_id": task.get("display_id"),
                    "id": task_id,
                    "found": True,
                    "title": _trim(task.get("title"), 200),
                    "status": status,
                    "assignee": task.get("assignee"),
                    "priority": task.get("priority"),
                    "result": _trim(task.get("result"), 500),
                    "body_preview": _trim(task.get("body"), 400),
                    "attention": sorted(set(attention)),
                    "recent_comments": [
                        {"author": c.get("author"), "created_at": c.get("created_at"), "body": _trim(c.get("body"), 350)}
                        for c in comments
                    ],
                    "recent_events": [
                        {"kind": e.get("kind"), "created_at": e.get("created_at"), "payload": _trim(e.get("payload"), 350)}
                        for e in events
                    ],
                    "recent_runs": [
                        {"outcome": r.get("outcome"), "started_at": r.get("started_at"), "ended_at": r.get("ended_at"), "summary": _trim(r.get("summary"), 350), "error": _trim(r.get("error"), 250)}
                        for r in runs
                    ],
                }
            )
    finally:
        conn.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ids", nargs="+", help="Kanban display IDs, e.g. K-1331 K-1350")
    ap.add_argument("--board", default=None, help="Kanban board slug (defaults to env/current board)")
    ap.add_argument("--limit", type=int, default=5, help="recent comments/events/runs per task")
    args = ap.parse_args(argv)
    ids = [i.upper() for i in args.ids if re.match(r"^[A-Za-z]+-\d+$", i)]
    if not ids:
        print(json.dumps({"ok": False, "error": "no valid K-#### ids supplied"}, indent=2))
        return 2
    print(json.dumps(compact_status(ids, board=args.board, limit=max(1, min(10, args.limit))), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
