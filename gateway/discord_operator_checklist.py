"""Compact Discord operator checklist formatting helpers.

This module is intentionally presentation-only.  It does not send, edit, or
rate-limit Discord messages; callers decide whether a status update is worth
posting.  Keep this helper safe to reuse from gateway adapters, skills, and
operator-facing scripts without changing gateway behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

ChecklistState = Literal["done", "active", "pending", "blocked", "skipped"]

_STATE_ICON: dict[str, str] = {
    "done": "✅",
    "active": "🔄",
    "pending": "☐",
    "blocked": "⚠️",
    "skipped": "➖",
}

_TERMINAL_STATES = {"done", "skipped"}


@dataclass(frozen=True)
class DiscordChecklistItem:
    """One line in a compact Discord operator checklist."""

    label: str
    state: ChecklistState = "pending"
    note: str | None = None


def _clean_inline(value: object, *, max_len: int = 120) -> str:
    """Return a single-line Discord-safe-ish snippet, not a raw log block.

    The checklist pattern is meant for human operator status, not tool dumps.
    Collapse whitespace and remove code fences/backticks so accidental command
    output does not become a multi-line raw log paste.
    """

    text = "" if value is None else str(value)
    text = text.replace("```", "").replace("`", "")
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 1)].rstrip() + "…"


def format_discord_operator_checklist(
    *,
    title: str,
    items: Iterable[DiscordChecklistItem | tuple[str, ChecklistState] | tuple[str, ChecklistState, str]],
    issue_id: str | None = None,
    summary: str | None = None,
    footer: str | None = None,
    max_items: int = 6,
) -> str:
    """Build a compact Discord status/checklist message.

    Use for sparse milestone updates on non-trivial operator tasks.  Do not use
    for every tool call; Discord already has typing/reaction affordances and
    high-frequency checklist posts become chat spam.

    Format:
        **BIF-533 — Discord compact operator checklist**
        2/4 complete · active
        ✅ inspect gateway/display helpers
        🔄 add reusable formatter
        ☐ document usage guidance
        ☐ verify tests/checks
        _quiet between milestones; no raw tool logs_
    """

    normalized: list[DiscordChecklistItem] = []
    for raw in items:
        if isinstance(raw, DiscordChecklistItem):
            item = raw
        else:
            label, state, *rest = raw
            item = DiscordChecklistItem(label=label, state=state, note=rest[0] if rest else None)
        if not item.label:
            continue
        state = item.state if item.state in _STATE_ICON else "pending"
        normalized.append(
            DiscordChecklistItem(
                label=_clean_inline(item.label, max_len=90),
                state=state,  # type: ignore[arg-type]
                note=_clean_inline(item.note, max_len=70) if item.note else None,
            )
        )

    visible = normalized[: max(1, max_items)]
    hidden_count = max(0, len(normalized) - len(visible))
    complete = sum(1 for item in normalized if item.state in _TERMINAL_STATES)
    total = len(normalized)
    has_active = any(item.state == "active" for item in normalized)
    has_blocked = any(item.state == "blocked" for item in normalized)
    phase = "blocked" if has_blocked else "active" if has_active else "complete" if total and complete == total else "queued"

    heading = _clean_inline(title, max_len=100)
    if issue_id:
        issue = _clean_inline(issue_id, max_len=24)
        if not heading.startswith(issue):
            heading = f"{issue} — {heading}"

    lines = [f"**{heading}**"]
    if summary:
        lines.append(_clean_inline(summary, max_len=140))
    if total:
        lines.append(f"{complete}/{total} complete · {phase}")
    else:
        lines.append(f"0/0 complete · {phase}")

    for item in visible:
        icon = _STATE_ICON.get(item.state, _STATE_ICON["pending"])
        line = f"{icon} {item.label}"
        if item.note:
            line += f" — {item.note}"
        lines.append(line)
    if hidden_count:
        lines.append(f"… +{hidden_count} more")
    if footer:
        lines.append(f"_{_clean_inline(footer, max_len=120)}_")

    return "\n".join(lines)


BIF_533_EXAMPLE = format_discord_operator_checklist(
    issue_id="BIF-533",
    title="Discord compact operator checklist progress pattern",
    summary="Implementing as a reusable formatter; gateway behavior unchanged.",
    items=[
        DiscordChecklistItem("inspect current gateway/display helpers", "done"),
        DiscordChecklistItem("define reusable Discord checklist format", "active"),
        DiscordChecklistItem("document when to post vs stay silent", "pending"),
        DiscordChecklistItem("run focused tests/checks", "pending"),
    ],
    footer="quiet between milestones; no raw tool logs",
)
