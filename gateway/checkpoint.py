"""Checkpoint helpers for durable state and high-risk operations.

This module provides checkpoint file management for non-trivial work
before high-risk operations.  Checkpoints are stored under:

    ``{HERMES_HOME}/working-set/checkpoints/{card_id}/``

Checkpoint lifecycle:
1. ``start_checkpoint()`` — Write intent.json before starting work
2. ``update_checkpoint_progress()`` — Update progress.json during work
3. ``finalize_checkpoint()`` — Write done.json on successful completion
4. ``read_checkpoint()`` — Read pending/progress/done state on restart
5. ``cleanup_abandoned_checkpoints()`` — Scrub checkpoints from crashed sessions
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _get_hermes_home() -> Path:
    return Path(
        os.getenv("HERMES_HOME") or str(Path.home() / ".hermes")
    ).expanduser().resolve()


def _get_checkpoint_dir(
    card_id: str,
    hermes_home: Optional[Path] = None,
) -> Path:
    """Return the checkpoint directory for a given card/operation ID.

    The card_id is sanitized to prevent directory traversal:
    - Only alphanumeric, hyphen, underscore, and dot are kept
    - Slashes and path separators are stripped
    """
    sanitized = re.sub(r"[^A-Za-z0-9_.-]", "_", card_id)[:128]
    base = hermes_home or _get_hermes_home()
    return base / "working-set" / "checkpoints" / sanitized


def _get_all_checkpoints_dir(hermes_home: Optional[Path] = None) -> Path:
    base = hermes_home or _get_hermes_home()
    return base / "working-set" / "checkpoints"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return json.loads(raw) if raw else None
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Checkpoint lifecycle
# ---------------------------------------------------------------------------


def start_checkpoint(
    card_id: str,
    *,
    description: str,
    expected_outcome: str,
    rollback_steps: list[str] | None = None,
    pre_state: dict[str, Any] | None = None,
    hermes_home: Optional[Path] = None,
) -> dict[str, Any]:
    """Start a new checkpoint for a high-risk operation.

    Args:
        card_id: BIF card or operation identifier (e.g. "BIF-1508").
        description: Human-readable description of the intent.
        expected_outcome: What a successful completion looks like.
        rollback_steps: Optional steps to undo the operation.
        pre_state: Optional snapshot of relevant state before mutation.

    Returns:
        The intent dict that was written to disk.
    """
    intent = {
        "schema": "biff.checkpoint.v1",
        "checkpoint_state": "pending",
        "card_id": card_id,
        "description": str(description),
        "expected_outcome": str(expected_outcome),
        "rollback_steps": list(rollback_steps or []),
        "pre_state": dict(pre_state or {}),
        "created_at": _utc_now_iso(),
        "start_timestamp": time.time(),
    }
    ckpt_dir = _get_checkpoint_dir(card_id, hermes_home)
    _write_json(ckpt_dir / "intent.json", intent)
    # Also write pre_state separately for easy comparison
    if pre_state:
        _write_json(ckpt_dir / "pre_state.json", pre_state)
    logger.info(
        "Checkpoint started: %s — %s",
        card_id,
        description[:80],
    )
    return intent


def update_checkpoint_progress(
    card_id: str,
    *,
    current_step: str,
    step_number: int,
    total_steps: int,
    detail: str | None = None,
    hermes_home: Optional[Path] = None,
) -> dict[str, Any]:
    """Update progress during a checkpointed operation.

    Args:
        card_id: BIF card or operation identifier.
        current_step: Description of the current step.
        step_number: 1-based step number.
        total_steps: Total expected steps.
        detail: Optional detail about what happened at this step.

    Returns:
        The progress dict that was written to disk.
    """
    progress = {
        "checkpoint_state": "in_progress",
        "card_id": card_id,
        "current_step": str(current_step),
        "step_number": step_number,
        "total_steps": total_steps,
        "detail": str(detail or ""),
        "updated_at": _utc_now_iso(),
        "update_timestamp": time.time(),
    }
    ckpt_dir = _get_checkpoint_dir(card_id, hermes_home)
    _write_json(ckpt_dir / "progress.json", progress)
    return progress


def finalize_checkpoint(
    card_id: str,
    *,
    result: str,
    verification_hash: str | None = None,
    post_state: dict[str, Any] | None = None,
    hermes_home: Optional[Path] = None,
) -> dict[str, Any]:
    """Finalize (mark done) a checkpoint after successful completion.

    Removes the pending/progress markers and writes a done.json.
    """
    done = {
        "schema": "biff.checkpoint.v1",
        "checkpoint_state": "done",
        "card_id": card_id,
        "result": str(result),
        "verification_hash": str(verification_hash or ""),
        "post_state": dict(post_state or {}),
        "completed_at": _utc_now_iso(),
        "completion_timestamp": time.time(),
    }
    ckpt_dir = _get_checkpoint_dir(card_id, hermes_home)
    _write_json(ckpt_dir / "done.json", done)
    # Clean up transient files
    for name in ("intent.json", "progress.json", "pre_state.json"):
        p = ckpt_dir / name
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    if post_state:
        _write_json(ckpt_dir / "post_state.json", post_state)
    logger.info("Checkpoint done: %s — %s", card_id, result[:60])
    return done


def read_checkpoint(
    card_id: str,
    hermes_home: Optional[Path] = None,
) -> dict[str, Any]:
    """Read the full checkpoint state for a card/operation.

    Returns a dict with keys:
        state (str): "pending", "in_progress", "done", or "not_found"
        intent (dict|None)
        progress (dict|None)
        done (dict|None)
        pre_state (dict|None)
        post_state (dict|None)
    """
    ckpt_dir = _get_checkpoint_dir(card_id, hermes_home)
    intent = _read_json(ckpt_dir / "intent.json")
    progress = _read_json(ckpt_dir / "progress.json")
    done = _read_json(ckpt_dir / "done.json")
    pre_state = _read_json(ckpt_dir / "pre_state.json")
    post_state = _read_json(ckpt_dir / "post_state.json")

    if done is not None:
        state = "done"
    elif progress is not None:
        state = "in_progress"
    elif intent is not None:
        state = "pending"
    else:
        state = "not_found"

    return {
        "state": state,
        "intent": intent,
        "progress": progress,
        "done": done,
        "pre_state": pre_state,
        "post_state": post_state,
    }


# ---------------------------------------------------------------------------
# Abandoned checkpoint cleanup
# ---------------------------------------------------------------------------


def list_all_checkpoints(
    hermes_home: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """List all checkpoints, returning their current state summaries."""
    base = _get_all_checkpoints_dir(hermes_home)
    if not base.exists():
        return []
    results: list[dict[str, Any]] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        card_id = entry.name
        results.append(read_checkpoint(card_id, hermes_home))
    return results


def cleanup_abandoned_checkpoints(
    *,
    max_age_seconds: float = 7 * 24 * 3600,  # 7 days default
    hermes_home: Optional[Path] = None,
) -> int:
    """Remove checkpoints that have been abandoned (pending/in-progress too long).

    Args:
        max_age_seconds: Age threshold for considering a checkpoint abandoned.
        hermes_home: Optional HERMES_HOME override.

    Returns:
        Number of checkpoint directories cleaned up.
    """
    base = _get_all_checkpoints_dir(hermes_home)
    if not base.exists():
        return 0
    now = time.time()
    cleaned = 0
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        card_id = entry.name
        state = read_checkpoint(card_id, hermes_home)
        if state["state"] in ("pending", "in_progress"):
            # Check age from intent
            intent = state.get("intent") or {}
            started = intent.get("start_timestamp", 0)
            if started and (now - started) > max_age_seconds:
                for f in entry.iterdir():
                    try:
                        f.unlink()
                    except OSError:
                        pass
                try:
                    entry.rmdir()
                    cleaned += 1
                    logger.info("Cleaned up abandoned checkpoint: %s", card_id)
                except OSError:
                    pass
    return cleaned