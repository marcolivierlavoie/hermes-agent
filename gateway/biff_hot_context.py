"""Cached fast-path context for Biff's live Discord turns."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home


_CACHE: dict[str, tuple[float, str]] = {}
_ROLE_RE = re.compile(r"\b(?:Biff|Ranger|Forge|Vex|Quill|Kanban|Linear|Mnemosyne|Obsidian|Discord)\b", re.IGNORECASE)


def _truthy(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _clip(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 32)].rstrip() + " ... [trimmed]"


def _soul_role_lines(limit: int = 900) -> list[str]:
    path = get_hermes_home() / "SOUL.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    picked: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) > 240:
            continue
        if _ROLE_RE.search(stripped):
            picked.append(stripped)
        if len("\n".join(picked)) >= limit:
            break
    return picked[:12]


def _kanban_lines(limit: int = 5) -> list[str]:
    try:
        from hermes_cli import kanban_db as kb

        conn = kb.connect()
        try:
            rows = kb.list_tasks(
                conn,
                include_archived=False,
                limit=limit,
                order_by="updated",
            )
        finally:
            conn.close()
    except Exception:
        return []
    out: list[str] = []
    for task in rows:
        display = getattr(task, "display_id", None) or getattr(task, "id", "")
        title = _clip(getattr(task, "title", ""), 120)
        status = getattr(task, "status", "")
        assignee = getattr(task, "assignee", None) or "unassigned"
        out.append(f"- {display}: {title} [{status}, {assignee}]")
    return out


def _kanban_config_line(config: Mapping[str, Any] | None) -> str | None:
    cfg = config if isinstance(config, Mapping) else {}
    kanban = cfg.get("kanban") if isinstance(cfg.get("kanban"), Mapping) else {}
    if not kanban:
        return None
    dispatch = kanban.get("dispatch_in_gateway")
    orchestration = kanban.get("orchestration") or kanban.get("orchestration_mode")
    pieces: list[str] = []
    if dispatch is not None:
        pieces.append(f"gateway dispatch={'on' if _truthy(dispatch) else 'off'}")
    if orchestration is not None:
        pieces.append(f"orchestration={orchestration}")
    return ", ".join(pieces) if pieces else None


def build_biff_hot_context(
    config: Mapping[str, Any] | None,
    *,
    platform_key: str | None,
    session_key: str | None = None,
    ttl_seconds: int = 30,
    query: str | None = None,
) -> str:
    """Return a small cached context capsule for live Discord turns.

    This is deliberately LLM-free and bounded. It gives Biff enough current
    local state to answer common questions without immediately opening memory,
    Kanban, or filesystem tools.
    """

    if str(platform_key or "").strip().lower() != "discord":
        return ""
    biff_cfg = (config or {}).get("biff") if isinstance((config or {}).get("biff"), Mapping) else {}
    platforms = biff_cfg.get("platforms") if isinstance(biff_cfg.get("platforms"), Mapping) else {}
    discord_cfg = platforms.get("discord") if isinstance(platforms.get("discord"), Mapping) else {}
    if not _truthy(discord_cfg.get("hot_context", True)):
        return ""

    key = str(session_key or "discord") + ":" + str(hash(str(query or "")))
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] <= max(1, int(ttl_seconds)):
        return cached[1]

    lines = [
        "## Biff Hot Context",
        "Use this small local snapshot before reaching for tools. Discord live-budget contract: answer direct/casual questions inline with no tools; use at most 1-2 narrow tool calls for quick checks; for multi-step verification, broad repo/archive/board work, or anything likely to need repeated checks, give the concise current answer and create/use a Kanban, role, or working-set continuation handle instead of exhausting the live turn.",
        "- Direct-query first: K-id status/attention questions should use the compact Kanban brief lane before file/session searches; shape SQL/Python/JSON output before it enters chat context.",
    ]
    kcfg = _kanban_config_line(config)
    if kcfg:
        lines.append(f"- Kanban config: {kcfg}.")
    role_lines = _soul_role_lines()
    if role_lines:
        lines.append("- Role/source-of-truth notes:")
        lines.extend(f"  - {_clip(line, 180)}" for line in role_lines)
    task_lines = _kanban_lines()
    if task_lines:
        lines.append("- Recent Kanban cards:")
        lines.extend(task_lines)
    try:
        from gateway.biff_fast_memory import build_biff_fast_memory_snapshot

        memory_snapshot = build_biff_fast_memory_snapshot(config, max_chars=650)
        if memory_snapshot:
            lines.append(memory_snapshot)
    except Exception:
        pass
    try:
        from agent.biff_rag_router import secondbrain_rag_context

        rag_context = secondbrain_rag_context(query or "", max_chars=900)
        if rag_context:
            lines.append(rag_context)
    except Exception:
        pass

    capsule = _clip("\n".join(lines), 3000)
    _CACHE[key] = (now, capsule)
    return capsule


def clear_biff_hot_context_cache() -> None:
    _CACHE.clear()
