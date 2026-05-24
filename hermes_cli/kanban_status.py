"""Compact direct-query Kanban status snapshots.

This module is the shared read path for K-id status / attention questions.
It intentionally resolves explicit task refs and reads the few task-scoped
rows needed for an answer; callers should use this before any broad board,
file, or session search when the user provides K-#### ids.
"""
from __future__ import annotations

from typing import Any, Optional

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_diagnostics as kd


def _public_task_ref(conn, task_ref: str) -> str:
    task = kb.get_task(conn, task_ref)
    if task is None:
        return str(task_ref)
    return task.display_id or task.id


def _publicize_task_refs(conn, value):
    if isinstance(value, dict):
        return {k: _publicize_task_refs(conn, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_publicize_task_refs(conn, v) for v in value]
    if isinstance(value, str):
        return _public_task_ref(conn, value)
    return value


def _comment_dict(conn, c: kb.Comment) -> dict[str, Any]:
    return {
        "id": c.id,
        "task_id": _public_task_ref(conn, c.task_id),
        "author": c.author,
        "body": c.body,
        "created_at": c.created_at,
    }


def _event_dict(conn, e: kb.Event) -> dict[str, Any]:
    return {
        "id": e.id,
        "task_id": _public_task_ref(conn, e.task_id),
        "kind": e.kind,
        "payload": _publicize_task_refs(conn, e.payload),
        "created_at": e.created_at,
        "run_id": e.run_id,
    }


def _latest_run_dict(r: Optional[kb.Run]) -> Optional[dict[str, Any]]:
    if r is None:
        return None
    return {
        "id": r.id,
        "profile": r.profile,
        "status": r.status,
        "outcome": r.outcome,
        "summary": r.summary,
        "error": r.error,
        "started_at": r.started_at,
        "ended_at": r.ended_at,
    }


def task_status_snapshot(
    conn,
    task_ref: str,
    *,
    recent_limit: int = 3,
) -> Optional[dict[str, Any]]:
    """Return a compact status/attention snapshot for one task ref.

    ``task_ref`` may be an internal id or display id (for example K-1364).
    The return shape is deliberately small enough to answer a chat question
    directly: status, assignee, result/summary, latest run, recent comments,
    recent events, and normalized attention indicators.
    """

    task = kb.get_task(conn, task_ref)
    if task is None:
        return None

    internal_id = task.id
    public_id = task.display_id or task.id
    comments = kb.list_comments(conn, internal_id)
    events = kb.list_events(conn, internal_id)
    runs = kb.list_runs(conn, internal_id)
    latest_summary = kb.latest_summary(conn, internal_id)
    diagnostics = [
        d.to_dict() for d in kd.compute_task_diagnostics(task, events, runs)
    ]

    attention: list[dict[str, Any]] = []
    if task.status == "blocked":
        reason = None
        for ev in reversed(events):
            if ev.kind == "blocked":
                payload = ev.payload or {}
                reason = payload.get("reason") if isinstance(payload, dict) else None
                break
        attention.append({"kind": "blocked", "severity": "error", "reason": reason})
    if task.status == "scheduled":
        attention.append({"kind": "scheduled", "severity": "warning"})
    if task.consecutive_failures:
        attention.append({
            "kind": "consecutive_failures",
            "severity": "warning",
            "count": task.consecutive_failures,
            "last_failure_error": task.last_failure_error,
        })
    for diag in diagnostics:
        attention.append({
            "kind": diag.get("kind"),
            "severity": diag.get("severity"),
            "title": diag.get("title"),
            "data": diag.get("data") or {},
        })

    return {
        "id": public_id,
        "display_id": public_id,
        "internal_id": internal_id,
        "title": task.title,
        "status": task.status,
        "assignee": task.assignee,
        "result": task.result,
        "latest_summary": latest_summary,
        "latest_run": _latest_run_dict(runs[-1] if runs else None),
        "attention": attention,
        "diagnostics": diagnostics,
        "recent_comments": [
            _comment_dict(conn, c) for c in comments[-max(0, recent_limit):]
        ],
        "recent_events": [
            _event_dict(conn, e) for e in events[-max(0, recent_limit):]
        ],
        "comment_count": len(comments),
        "event_count": len(events),
        "run_count": len(runs),
    }


def task_status_snapshots(
    conn,
    task_refs: list[str],
    *,
    recent_limit: int = 3,
) -> dict[str, Any]:
    """Return compact status snapshots plus explicit missing refs."""

    tasks: list[dict[str, Any]] = []
    missing: list[str] = []
    for ref in task_refs:
        snap = task_status_snapshot(conn, ref, recent_limit=recent_limit)
        if snap is None:
            missing.append(ref)
        else:
            tasks.append(snap)
    return {"tasks": tasks, "missing": missing, "count": len(tasks)}
