"""Thin per-turn long-turn governor primitives.

This module is intentionally small and side-effect-light.  It tracks a single
agent turn, emits machine-readable runtime signals, persists resumable packets at
thresholds, and formats quiet checkpoint text.  Runtime integration owns when to
show or act on those signals.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from utils import atomic_json_write


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LongTurnThresholds:
    """Thresholds that trigger a persisted resume packet.

    Defaults are conservative: they do not halt execution and only create a small
    JSON packet once a turn is genuinely long-running.
    """

    api_calls: int = 30
    tool_calls: int = 60
    elapsed_seconds: float = 20 * 60
    repeated_failure_warnings: int = 3

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "LongTurnThresholds":
        if not isinstance(data, Mapping):
            return cls()
        default = cls()
        return cls(
            api_calls=_positive_int(data.get("api_calls"), default.api_calls),
            tool_calls=_positive_int(data.get("tool_calls"), default.tool_calls),
            elapsed_seconds=float(_positive_int(data.get("elapsed_seconds"), int(default.elapsed_seconds))),
            repeated_failure_warnings=_positive_int(
                data.get("repeated_failure_warnings"), default.repeated_failure_warnings
            ),
        )


@dataclass
class EvidenceEntry:
    kind: str
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "metadata": self.metadata,
            "ts": self.ts,
        }


@dataclass
class LongTurnState:
    """Per-turn state and counters for the long-turn governor."""

    session_id: str
    task_id: str
    turn_id: str
    started_at: float = field(default_factory=time.time)
    api_calls: int = 0
    tool_calls: int = 0
    guardrail_warnings: int = 0
    guardrail_halts: int = 0
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    evidence_ledger: list[EvidenceEntry] = field(default_factory=list)
    role_handoffs: list[dict[str, Any]] = field(default_factory=list)
    resume_packet_path: str | None = None
    resume_packet_error: str | None = None
    threshold_reasons: list[str] = field(default_factory=list)
    last_signal: str = "started"

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def to_metrics(self, *, exit_reason: str | None = None) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "api_calls": self.api_calls,
            "tool_calls": self.tool_calls,
            "guardrail_warnings": self.guardrail_warnings,
            "guardrail_halts": self.guardrail_halts,
            "checkpoint_count": len(self.checkpoints),
            "evidence_count": len(self.evidence_ledger),
            "handoff_count": len(self.role_handoffs),
            "threshold_reasons": list(self.threshold_reasons),
            "resume_packet_path": self.resume_packet_path,
            "resume_packet_error": self.resume_packet_error,
            "last_signal": self.last_signal,
            "exit_reason": exit_reason,
        }

    def runtime_signal(self) -> dict[str, Any]:
        return {
            "signal": self.last_signal,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "api_calls": self.api_calls,
            "tool_calls": self.tool_calls,
            "threshold_reasons": list(self.threshold_reasons),
            "resume_packet_path": self.resume_packet_path,
            "resume_packet_error": self.resume_packet_error,
        }


class LongTurnGovernor:
    """No-halt governor for long-running turns.

    The governor never restarts processes, mutates config, or blocks tools.  It
    only records signals and persists a resume packet when thresholds are crossed.
    """

    def __init__(
        self,
        *,
        state: LongTurnState,
        thresholds: LongTurnThresholds | None = None,
        persist_dir: Path | str | None = None,
    ) -> None:
        self.state = state
        self.thresholds = thresholds or LongTurnThresholds()
        self.persist_dir = Path(persist_dir) if persist_dir is not None else None
        self._persisted_reasons: set[str] = set()

    def mark_api_call(self, count: int | None = None) -> dict[str, Any]:
        self.state.api_calls = int(count) if count is not None else self.state.api_calls + 1
        self.state.last_signal = "api_call"
        return self._check_thresholds()

    def mark_tool_call(self, tool_name: str, *, failed: bool = False) -> dict[str, Any]:
        self.state.tool_calls += 1
        self.state.last_signal = "tool_call_failed" if failed else "tool_call"
        if failed:
            self.add_evidence("tool_failure", f"{tool_name} failed", {"tool_name": tool_name})
        return self._check_thresholds()

    def observe_guardrail(self, decision: Any) -> dict[str, Any]:
        action = getattr(decision, "action", "") or ""
        code = getattr(decision, "code", "") or ""
        if action == "warn":
            self.state.guardrail_warnings += 1
            self.state.last_signal = "guardrail_warning"
            self.add_evidence(
                "guardrail_warning",
                f"{getattr(decision, 'tool_name', '')}: {code}",
                _decision_metadata(decision),
            )
        elif action in {"block", "halt"}:
            self.state.guardrail_halts += 1
            self.state.last_signal = "guardrail_halt"
            self.add_evidence(
                "guardrail_halt",
                f"{getattr(decision, 'tool_name', '')}: {code}",
                _decision_metadata(decision),
            )
        return self._check_thresholds()

    def add_checkpoint(self, label: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        checkpoint = {
            "label": label,
            "metadata": dict(metadata or {}),
            "ts": time.time(),
            "elapsed_seconds": round(self.state.elapsed_seconds, 3),
            "api_calls": self.state.api_calls,
            "tool_calls": self.state.tool_calls,
        }
        self.state.checkpoints.append(checkpoint)
        self.state.last_signal = "checkpoint"
        return checkpoint

    def add_evidence(self, kind: str, summary: str, metadata: Mapping[str, Any] | None = None) -> None:
        self.state.evidence_ledger.append(EvidenceEntry(kind, summary, dict(metadata or {})))

    def add_role_handoff(
        self,
        *,
        from_role: str,
        to_role: str,
        reason: str,
        evidence_refs: list[int] | None = None,
    ) -> dict[str, Any]:
        handoff = {
            "from_role": from_role,
            "to_role": to_role,
            "reason": reason,
            "evidence_refs": list(evidence_refs or []),
            "ts": time.time(),
        }
        self.state.role_handoffs.append(handoff)
        self.state.last_signal = "role_handoff"
        self._persist_resume_packet("role_handoff")
        return handoff

    def quiet_checkpoint_text(self, label: str | None = None) -> str:
        return render_quiet_checkpoint(self.state, label=label)

    def summary_metrics(self, *, exit_reason: str | None = None) -> dict[str, Any]:
        return self.state.to_metrics(exit_reason=exit_reason)

    def persist_resume_packet(self, reason: str = "manual") -> Path | None:
        return self._persist_resume_packet(reason)

    def _check_thresholds(self) -> dict[str, Any]:
        reasons: list[str] = []
        if self.state.api_calls >= self.thresholds.api_calls:
            reasons.append("api_calls")
        if self.state.tool_calls >= self.thresholds.tool_calls:
            reasons.append("tool_calls")
        if self.state.elapsed_seconds >= self.thresholds.elapsed_seconds:
            reasons.append("elapsed_seconds")
        if self.state.guardrail_warnings >= self.thresholds.repeated_failure_warnings:
            reasons.append("repeated_failure_warnings")

        new_reasons = [reason for reason in reasons if reason not in self.state.threshold_reasons]
        if new_reasons:
            self.state.threshold_reasons.extend(new_reasons)
            self.state.last_signal = "threshold_crossed"
            for reason in new_reasons:
                self._persist_resume_packet(reason)
        return self.state.runtime_signal()

    def _persist_resume_packet(self, reason: str) -> Path | None:
        if self.persist_dir is None:
            return None
        key = reason or "threshold"
        if key in self._persisted_reasons and self.state.resume_packet_path:
            return Path(self.state.resume_packet_path)
        try:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            safe_session = _safe_component(self.state.session_id or "no-session")
            safe_turn = _safe_component(self.state.turn_id or "turn")
            path = self.persist_dir / safe_session / f"{safe_turn}.json"
            self.state.resume_packet_path = str(path)
            packet = build_resume_packet(self.state, reason=reason)
            atomic_json_write(path, packet)
        except Exception as exc:
            self.state.resume_packet_path = None
            self.state.resume_packet_error = f"{type(exc).__name__}: {exc}"
            self.state.last_signal = "resume_packet_error"
            logger.warning("long-turn resume packet persistence failed: %s", exc)
            return None
        self.state.resume_packet_error = None
        self._persisted_reasons.add(key)
        return path


def build_resume_packet(state: LongTurnState, *, reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "reason": reason,
        "created_at": time.time(),
        "runtime_signal": state.runtime_signal(),
        "summary_metrics": state.to_metrics(),
        "latest_checkpoint": state.checkpoints[-1] if state.checkpoints else None,
        "evidence_ledger": [entry.to_dict() for entry in state.evidence_ledger],
        "role_handoffs": list(state.role_handoffs),
        "resume_instructions": (
            "Continue from the latest checkpoint, cite evidence entries used, "
            "and avoid repeating guardrail-triggering calls unchanged."
        ),
    }


def render_quiet_checkpoint(state: LongTurnState, *, label: str | None = None) -> str:
    bits = [
        label or "long-turn checkpoint",
        f"api={state.api_calls}",
        f"tools={state.tool_calls}",
        f"elapsed={int(state.elapsed_seconds)}s",
    ]
    if state.threshold_reasons:
        bits.append("threshold=" + ",".join(state.threshold_reasons))
    if state.resume_packet_path:
        bits.append(f"resume={state.resume_packet_path}")
    return "⏱ " + " | ".join(bits)


def _decision_metadata(decision: Any) -> dict[str, Any]:
    if hasattr(decision, "to_metadata"):
        try:
            data = decision.to_metadata()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "action": getattr(decision, "action", ""),
        "code": getattr(decision, "code", ""),
        "tool_name": getattr(decision, "tool_name", ""),
        "count": getattr(decision, "count", 0),
    }


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def _safe_component(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value)
    return safe[:120] or "unknown"
