"""Small LLM-free memory snapshots for Biff's live Discord path."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping


_SECRET_RE = re.compile(
    r"(?i)(api[_ -]?key|password|passwd|private[_ -]?key|secret|token|credential)\s*[:=]\s*\S+"
)


def _clip(value: Any, limit: int) -> str:
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=[redacted]", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 18)].rstrip() + " ... [trimmed]"


def _truthy(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _provider() -> Any | None:
    try:
        from plugins.memory import load_memory_provider

        return load_memory_provider("mnemosyne")
    except Exception:
        return None


def _tool_json(provider: Any, action: str, **kwargs: Any) -> dict[str, Any]:
    try:
        raw = provider.handle_tool_call("mnemosyne_memory", {"action": action, **kwargs})
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _safe_recall_lines(provider: Any, query: str, *, limit: int = 3) -> list[str]:
    if not query.strip():
        return []
    try:
        recalled = provider.recall(query, limit=limit, include_suppressed=False)
    except Exception:
        recalled = []
    lines: list[str] = []
    for item in recalled[:limit]:
        memory = item.get("memory") if isinstance(item, Mapping) else None
        if not isinstance(memory, Mapping):
            continue
        if memory.get("current_request_safe") is not True:
            continue
        if str(memory.get("sensitivity") or "").strip().lower() not in {"non_sensitive", "public"}:
            continue
        content = _clip(memory.get("content"), 170)
        source = _clip(memory.get("source"), 80)
        topic = _clip(memory.get("topic") or memory.get("context"), 80)
        memory_id = _clip(memory.get("id"), 48)
        if content:
            suffix = f" ({source or topic}; {memory_id})".strip()
            lines.append(f"- {content}{suffix}")
    return lines


def build_biff_fast_memory_snapshot(
    config: Mapping[str, Any] | None,
    *,
    query: str = "Biff Discord Kanban Mnemosyne Obsidian Linear source of truth roles",
    max_chars: int = 1400,
) -> str:
    """Return a compact Mnemosyne snapshot without model calls or mutation."""

    cfg = config if isinstance(config, Mapping) else {}
    biff_cfg = cfg.get("biff") if isinstance(cfg.get("biff"), Mapping) else {}
    platforms = biff_cfg.get("platforms") if isinstance(biff_cfg.get("platforms"), Mapping) else {}
    discord_cfg = platforms.get("discord") if isinstance(platforms.get("discord"), Mapping) else {}
    if not _truthy(discord_cfg.get("fast_memory_context", True)):
        return ""

    provider = _provider()
    if provider is None:
        return ""

    digest = _tool_json(provider, "memory_digest")
    policy = _tool_json(provider, "recall_policy")
    if not digest.get("success") and not policy.get("success"):
        return ""

    lines = [
        "## Biff Fast Memory Snapshot",
        "LLM-free Mnemosyne snapshot. Use as compact memory context before opening memory tools.",
    ]
    needs = digest.get("needs_decision") if isinstance(digest.get("needs_decision"), Mapping) else {}
    fyi = digest.get("fyi") if isinstance(digest.get("fyi"), Mapping) else {}
    if fyi:
        lines.append(
            "- Memory state: "
            f"{int(fyi.get('trusted_memory_count') or 0)} trusted, "
            f"{int(fyi.get('active_suppression_count') or 0)} suppressed, "
            f"{int(needs.get('pending_candidate_count') or 0)} pending candidates."
        )
    conflict_ids = needs.get("conflict_memory_ids") if isinstance(needs.get("conflict_memory_ids"), list) else []
    if conflict_ids:
        lines.append("- Active memory conflicts: " + ", ".join(_clip(x, 48) for x in conflict_ids[:5]) + ".")
    authority = policy.get("authority_order") if isinstance(policy.get("authority_order"), list) else []
    if authority:
        lines.append("- Recall authority: " + " > ".join(_clip(x, 42) for x in authority[:5]) + ".")
    recall_lines = _safe_recall_lines(provider, query, limit=3)
    if recall_lines:
        lines.append("- Safe matched memories:")
        lines.extend(recall_lines)
    snapshot = "\n".join(lines)
    return _clip(snapshot, max_chars)
