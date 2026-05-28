"""Cached fast-path context for Biff's live Discord turns."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home


_CACHE: dict[str, tuple[float, str]] = {}
_ROLE_RE = re.compile(r"\b(?:Biff|Ranger|Forge|Vex|Quill|Kanban|Mnemosyne|Obsidian|Discord)\b", re.IGNORECASE)


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


def _discord_biff_config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    cfg = config if isinstance(config, Mapping) else {}
    raw_biff = cfg.get("biff")
    biff_cfg = raw_biff if isinstance(raw_biff, Mapping) else {}
    raw_platforms = biff_cfg.get("platforms")
    platforms = raw_platforms if isinstance(raw_platforms, Mapping) else {}
    raw_discord = platforms.get("discord")
    return raw_discord if isinstance(raw_discord, Mapping) else {}


def _compact_identity_enabled(discord_cfg: Mapping[str, Any]) -> bool:
    """Return whether Biff's compact identity tier may replace hot context.

    Rollback knobs:
    - config: biff.platforms.discord.compact_identity: false
    - env: HERMES_BIFF_COMPACT_IDENTITY=0
    """

    import os

    env_value = os.getenv("HERMES_BIFF_COMPACT_IDENTITY")
    if env_value is not None:
        return _truthy(env_value, default=True)
    return _truthy(discord_cfg.get("compact_identity", True), default=True)


def should_use_biff_compact_identity(
    config: Mapping[str, Any] | None,
    *,
    platform_key: str | None,
    query: str | None = None,
) -> bool:
    """Choose compact identity for short direct Discord turns only.

    This intentionally stays conservative: any memory/history/repo/Kanban/tool
    work keeps the full hot-context capsule, while simple answer-now turns get a
    small policy identity that preserves the non-negotiable Biff contract.
    """

    if str(platform_key or "").strip().lower() != "discord":
        return False
    discord_cfg = _discord_biff_config(config)
    if not _compact_identity_enabled(discord_cfg):
        return False
    if not str(query or "").strip():
        return False
    try:
        from agent.biff_intent_router import plan_biff_turn
        from gateway.biff_memory_tiers import classify_biff_memory_tier

        plan = plan_biff_turn(str(query or ""), command=False)
        memory_tier = classify_biff_memory_tier(query or "", plan)
    except Exception:
        return False
    return (
        plan.action == "answer_now"
        and plan.runtime == "direct_answer"
        and memory_tier.tier == "no-memory"
    )


def build_biff_compact_identity(config: Mapping[str, Any] | None = None) -> str:
    """Return a minimal, policy-complete Biff identity for no-tool live turns."""

    lines = [
        "## Biff Compact Identity",
        "- Discord #hermes is Marco's primary live command surface; answer first, concise, action-led, and stay on the current thread.",
        "- Native Hermes Kanban board `biff-os` is the source of truth for Biff OS work; use BIF-### in human-facing updates.",
        "- Role handoff is explicit-consent only: Forge/Vex/Quill/Ranger role names alone are conversation, not approval; never dispatch from quoted/prior text.",
        "- Use tools when required for correctness; direct/casual replies should stay no-tool, quick checks should be narrow, and broad work should use Kanban or working-set continuation handles.",
        "- Keep safety gates: ask before spend, external sends, destructive/security/access changes, protected merges, or private/family-sensitive actions.",
        "- Care/Spark/Radar stay active: be caring, occasionally creative, and notice leverage without pushing productivity through tenderness.",
        "- Rollback: set `biff.platforms.discord.compact_identity: false` or `HERMES_BIFF_COMPACT_IDENTITY=0` to force full hot context.",
    ]
    kcfg = _kanban_config_line(config)
    if kcfg:
        lines.append(f"- Kanban config: {kcfg}.")
    return _clip("\n".join(lines), 1400)


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
    discord_cfg = _discord_biff_config(config)
    if not _truthy(discord_cfg.get("hot_context", True)):
        return ""

    key = str(session_key or "discord") + ":" + str(hash(str(query or "")))
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] <= max(1, int(ttl_seconds)):
        return cached[1]

    try:
        from agent.biff_intent_router import plan_biff_turn
        from gateway.biff_memory_tiers import classify_biff_memory_tier

        turn_plan = plan_biff_turn(str(query or ""), command=False)
        memory_tier = classify_biff_memory_tier(query or "", turn_plan)
    except Exception:
        turn_plan = None
        memory_tier = None

    if should_use_biff_compact_identity(config, platform_key=platform_key, query=query):
        capsule = build_biff_compact_identity(config)
        _CACHE[key] = (now, capsule)
        return capsule

    if memory_tier is not None and memory_tier.tier == "no-memory":
        capsule = build_biff_compact_identity(config)
        _CACHE[key] = (now, capsule)
        return capsule

    lines = [
        "## Biff Hot Context",
        "Use this small local snapshot before reaching for tools. Discord live-budget contract: answer direct/casual questions inline with no tools; use at most 1-2 narrow tool calls for quick checks; for multi-step verification, broad repo/archive/board work, or anything likely to need repeated checks, give the concise current answer and create/use a Kanban, role, or working-set continuation handle instead of exhausting the live turn.",
        "- Direct-query first: K-id status/attention questions should use the compact Kanban brief lane before file/session searches; shape SQL/Python/JSON output before it enters chat context.",
    ]
    if memory_tier is not None:
        lines.append(f"- Memory tier: {memory_tier.tier} — {memory_tier.reason}.")
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
    if memory_tier is not None and memory_tier.allow_mnemosyne_snapshot:
        try:
            from gateway.biff_fast_memory import build_biff_fast_memory_snapshot

            memory_snapshot = build_biff_fast_memory_snapshot(
                config,
                query=query or "Biff Discord Kanban Mnemosyne Obsidian source of truth roles",
                max_chars=memory_tier.max_snapshot_chars,
            )
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
