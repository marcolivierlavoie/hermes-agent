"""Compact direct-query helpers for Biff Kanban status/attention checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

_K_ID_RE = re.compile(r"\bK-\d+\b", re.IGNORECASE)
_BLOCKING_WORD_RE = re.compile(r"\b(blocked|blocker|blocking|attention|needs marco|approval|stuck|failed|fail|manual|reopen)\b", re.IGNORECASE)


@dataclass(frozen=True)
class KanbanBrief:
    id: str
    title: str
    status: str
    assignee: str | None
    result: str | None
    recent_comments: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]
    attention: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "assignee": self.assignee,
            "result": self.result,
            "recent_comments": self.recent_comments,
            "recent_events": self.recent_events,
            "attention": self.attention,
        }


def extract_k_ids(text: Any) -> list[str]:
    """Return distinct K-ids in first-seen order."""

    seen: set[str] = set()
    out: list[str] = []
    for match in _K_ID_RE.finditer(str(text or "")):
        kid = match.group(0).upper()
        if kid not in seen:
            seen.add(kid)
            out.append(kid)
    return out


def _clip(value: Any, limit: int = 280) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + " ... [trimmed]"


def _comment_to_dict(comment: Any) -> dict[str, Any]:
    return {
        "author": getattr(comment, "author", None),
        "body": _clip(getattr(comment, "body", None), 320),
        "created_at": getattr(comment, "created_at", None),
    }


def _event_to_dict(event: Any) -> dict[str, Any]:
    payload = getattr(event, "payload", None)
    if isinstance(payload, Mapping):
        payload = _compact_payload(payload)
    return {
        "kind": getattr(event, "kind", None),
        "payload": payload,
        "created_at": getattr(event, "created_at", None),
    }


def _compact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in sorted(payload.keys()):
        value = payload[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            compact[str(key)] = _clip(value, 180) if isinstance(value, str) else value
        elif isinstance(value, list):
            compact[str(key)] = [_clip(v, 120) for v in value[:5]]
        else:
            compact[str(key)] = _clip(json.dumps(value, sort_keys=True, default=str), 180)
    return compact


def _attention_for(task: Any, comments: Iterable[Any], events: Iterable[Any]) -> list[str]:
    attention: list[str] = []
    status = str(getattr(task, "status", "") or "").lower()
    if status == "blocked":
        attention.append("status=blocked")
    result = getattr(task, "result", None)
    if result and _BLOCKING_WORD_RE.search(str(result)):
        attention.append("result_mentions_blocker")
    for comment in list(comments)[-3:]:
        body = getattr(comment, "body", "") or ""
        if _BLOCKING_WORD_RE.search(body):
            attention.append("recent_comment_mentions_attention")
            break
    for event in list(events)[-5:]:
        kind = str(getattr(event, "kind", "") or "")
        payload = getattr(event, "payload", None)
        if "block" in kind.lower() or _BLOCKING_WORD_RE.search(json.dumps(payload, default=str)):
            attention.append("recent_event_mentions_blocker")
            break
    return sorted(set(attention))


def build_kanban_brief(refs: Iterable[str], *, board: str | None = None, recent_limit: int = 3) -> dict[str, Any]:
    """Directly query Kanban for compact status/attention data.

    This intentionally bypasses filesystem/session searches for K-id status questions.
    """

    from hermes_cli import kanban_db as kb

    conn = kb.connect(board=board)
    try:
        items: list[dict[str, Any]] = []
        missing: list[str] = []
        for ref in refs:
            task = kb.get_task(conn, ref)
            if task is None:
                missing.append(str(ref))
                continue
            comments = kb.list_comments(conn, task.id)
            events = kb.list_events(conn, task.id)
            brief = KanbanBrief(
                id=getattr(task, "display_id", None) or getattr(task, "id", str(ref)),
                title=_clip(getattr(task, "title", ""), 240) or "",
                status=getattr(task, "status", ""),
                assignee=getattr(task, "assignee", None),
                result=_clip(getattr(task, "result", None), 400),
                recent_comments=[_comment_to_dict(c) for c in comments[-recent_limit:]],
                recent_events=[_event_to_dict(e) for e in events[-recent_limit:]],
                attention=_attention_for(task, comments, events),
            )
            items.append(brief.to_dict())
    finally:
        conn.close()
    return {"items": items, "missing": missing, "count": len(items)}


def build_kanban_brief_from_text(text: Any, *, board: str | None = None, recent_limit: int = 3) -> dict[str, Any]:
    return build_kanban_brief(extract_k_ids(text), board=board, recent_limit=recent_limit)


def render_kanban_brief(brief: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for item in brief.get("items", []) or []:
        attention = item.get("attention") or []
        suffix = f" attention={','.join(attention)}" if attention else " attention=none"
        lines.append(
            f"{item.get('id')}: {item.get('status')} / {item.get('assignee') or 'unassigned'} — {item.get('title')}{suffix}"
        )
        if item.get("result"):
            lines.append(f"  result: {item['result']}")
        comments = item.get("recent_comments") or []
        if comments:
            last = comments[-1]
            lines.append(f"  last comment: {last.get('author') or '?'} — {last.get('body') or ''}")
        events = item.get("recent_events") or []
        if events:
            lines.append("  recent events: " + ", ".join(str(e.get("kind")) for e in events if e.get("kind")))
    missing = brief.get("missing") or []
    if missing:
        lines.append("missing: " + ", ".join(str(x) for x in missing))
    return "\n".join(lines) if lines else "No K-ids found."
