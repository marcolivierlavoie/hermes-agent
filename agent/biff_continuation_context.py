"""Lightweight Biff continuation checkpoints for chat refresh recovery.

These helpers intentionally do not make Kanban the universal source of truth.
They provide a scratch/session checkpoint layer for ordinary conversations and
informal workstreams, while still surfacing K-card references when the active
thread actually has them.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - fallback for isolated imports
    get_hermes_home = None  # type: ignore[assignment]


_REFRESH_RESUME_RE = re.compile(
    r"("
    r"\b(?:chat|tab|window|dashboard|browser|page|ui)\b.*\b(?:refresh(?:ed)?|reload(?:ed)?|closed|lost|disappear(?:ed)?)\b"
    r"|\b(?:refresh(?:ed)?|reload(?:ed)?|closed)\b.*\b(?:chat|tab|window|dashboard|browser|page|ui)\b"
    r"|\b(?:can'?t|cannot|don'?t)\s+(?:see|find)\s+(?:your\s+)?(?:progress|context|status|work)\b"
    r"|\b(?:where\s+did\s+we\s+leave\s+off|what\s+were\s+we\s+(?:doing|discussing|working\s+on))\b"
    r"|\b(?:resume|continue|pick\s+up)\s+(?:this\s+)?(?:conversation|thread|context|non[-\s]?kanban\s+thing)\b"
    r"|\bpick\s+up\s+the\s+non[-\s]?kanban\s+thing\b"
    r"|^\s*resume\s*[.!?]*\s*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_KANBAN_REF_RE = re.compile(r"\bK-\d+\b", re.IGNORECASE)
_MAX_SNIPPET_CHARS = 600


@dataclass(frozen=True)
class ContinuationCheckpoint:
    session_key: str
    platform: str
    updated_at: int
    context_kind: str
    user_text: str
    assistant_text: str
    route_action: str | None
    route_reason: str | None
    kanban_refs: list[str]
    next_resume_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_refresh_resume_prompt(text: Any) -> bool:
    """Return True when the user is asking to recover lost visible context."""

    body = " ".join(str(text or "").strip().split())
    if not body:
        return False
    return bool(_REFRESH_RESUME_RE.search(body))


def continuation_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    if get_hermes_home is not None:
        try:
            return Path(get_hermes_home()) / "working-set" / "live-context"
        except Exception:
            pass
    return Path.home() / ".hermes" / "working-set" / "live-context"


def _safe_session_slug(session_key: str) -> str:
    digest = hashlib.sha256(str(session_key).encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"session-{digest}"


def checkpoint_path_for_session(session_key: str, *, root: str | Path | None = None) -> Path:
    return continuation_root(root) / f"{_safe_session_slug(session_key)}.json"


def _snippet(text: Any, *, limit: int = _MAX_SNIPPET_CHARS) -> str:
    body = " ".join(str(text or "").strip().split())
    if len(body) <= limit:
        return body
    return body[: max(0, limit - 1)].rstrip() + "…"


def _extract_kanban_refs(*texts: Any) -> list[str]:
    refs: set[str] = set()
    for text in texts:
        for match in _KANBAN_REF_RE.findall(str(text or "")):
            refs.add(match.upper())
    return sorted(refs, key=lambda ref: int(ref.split("-", 1)[1]))


def write_continuation_checkpoint(
    *,
    session_key: str,
    platform: str | None,
    user_text: Any,
    assistant_text: Any,
    route_action: str | None = None,
    route_reason: str | None = None,
    root: str | Path | None = None,
) -> ContinuationCheckpoint:
    """Persist the latest lightweight continuation state for a live chat.

    This is deliberately a scratch checkpoint, not a task ledger.  If K-card
    refs are present we mark the checkpoint as Kanban-backed; otherwise it stays
    a non-Kanban scratch context so ordinary conversations are not forced into
    the board.
    """

    root_path = continuation_root(root)
    root_path.mkdir(parents=True, exist_ok=True)
    user_snippet = _snippet(user_text)
    assistant_snippet = _snippet(assistant_text)
    refs = _extract_kanban_refs(user_snippet, assistant_snippet)
    context_kind = "kanban" if refs else "scratch"
    next_action = (
        f"Check Kanban refs {', '.join(refs)} plus recent session history before answering."
        if refs
        else "Use recent session history plus this scratch checkpoint before asking Marco to reconstruct context."
    )
    checkpoint = ContinuationCheckpoint(
        session_key=str(session_key),
        platform=str(platform or "gateway"),
        updated_at=int(time.time()),
        context_kind=context_kind,
        user_text=user_snippet,
        assistant_text=assistant_snippet,
        route_action=route_action,
        route_reason=route_reason,
        kanban_refs=refs,
        next_resume_action=next_action,
    )
    payload = checkpoint.to_dict()
    checkpoint_path_for_session(str(session_key), root=root_path).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root_path / "current.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return checkpoint


def load_continuation_checkpoint(
    *,
    session_key: str | None = None,
    root: str | Path | None = None,
) -> ContinuationCheckpoint | None:
    root_path = continuation_root(root)
    path = checkpoint_path_for_session(session_key, root=root_path) if session_key else root_path / "current.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ContinuationCheckpoint(
            session_key=str(data.get("session_key") or ""),
            platform=str(data.get("platform") or "gateway"),
            updated_at=int(data.get("updated_at") or 0),
            context_kind=str(data.get("context_kind") or "scratch"),
            user_text=str(data.get("user_text") or ""),
            assistant_text=str(data.get("assistant_text") or ""),
            route_action=data.get("route_action"),
            route_reason=data.get("route_reason"),
            kanban_refs=[str(ref) for ref in (data.get("kanban_refs") or [])],
            next_resume_action=str(data.get("next_resume_action") or "Use recent session history before answering."),
        )
    except Exception:
        return None


def build_resume_context_injection(
    prompt: Any,
    *,
    session_key: str | None = None,
    root: str | Path | None = None,
) -> str | None:
    """Build an agent-facing resume note for refresh/context-recovery prompts."""

    if not is_refresh_resume_prompt(prompt):
        return None
    checkpoint = load_continuation_checkpoint(session_key=session_key, root=root)
    lines = [
        "[Biff refresh/resume context]",
        "The user is trying to recover context after a refresh or lost visible chat state.",
        "First inspect recent session history and any relevant working-set/live process state before asking the user to reconstruct context.",
    ]
    if checkpoint is None:
        lines.extend(
            [
                "No lightweight checkpoint was found for this session.",
                "Use session_search/recent transcript evidence first; use Kanban only if the recovered thread was actually task-backed.",
            ]
        )
    else:
        lines.extend(
            [
                f"Context kind: {checkpoint.context_kind}",
                f"Checkpoint updated_at: {checkpoint.updated_at}",
                f"Previous user turn: {checkpoint.user_text}",
                f"Previous assistant result: {checkpoint.assistant_text}",
                f"Route: {checkpoint.route_action or 'unknown'} — {checkpoint.route_reason or 'unknown'}",
                f"Next resume action: {checkpoint.next_resume_action}",
            ]
        )
        if checkpoint.kanban_refs:
            lines.append(f"Check Kanban refs: {', '.join(checkpoint.kanban_refs)}")
        else:
            lines.append("Do not force this into Kanban; treat it as a scratch conversation unless new work needs a task.")
    lines.extend(["[/Biff refresh/resume context]", "", str(prompt or "")])
    return "\n".join(lines)
