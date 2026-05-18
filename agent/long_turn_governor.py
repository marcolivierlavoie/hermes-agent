"""Thin per-turn long-turn governor primitives.

This module is intentionally small and side-effect-light.  It tracks a single
agent turn, emits machine-readable runtime signals, persists resumable packets at
thresholds, and formats quiet checkpoint text.  Runtime integration owns when to
show or act on those signals.
"""

from __future__ import annotations

import json
import logging
import hashlib
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
    fallback_decision: dict[str, Any] | None = None
    changed_hypothesis_retry: dict[str, Any] | None = None
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
            "fallback_decision": self.fallback_decision,
            "changed_hypothesis_retry": self.changed_hypothesis_retry,
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
            "fallback_decision": self.fallback_decision,
            "changed_hypothesis_retry": self.changed_hypothesis_retry,
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
        self._failure_observations: dict[str, dict[str, Any]] = {}

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

    def observe_tool_failure(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: Any,
    ) -> dict[str, Any]:
        """Observe a failed tool/test/error tuple and decide whether to fall back.

        Two identical failed attempts with no changed hypothesis are treated as a
        loop and produce a pause/fallback decision. If the retry includes a
        changed hypothesis, that one retry is allowed; a subsequent failure for
        the same tool/test/error pauses the turn.
        """

        if self.state.fallback_decision is not None:
            return self.state.runtime_signal()
        failure_key, metadata = _failure_signature(tool_name, args, result)
        hypothesis_hash = metadata["hypothesis_hash"]
        record = self._failure_observations.get(failure_key)
        if record is None:
            self._failure_observations[failure_key] = {
                "count": 1,
                "hypotheses": {hypothesis_hash},
                "changed_retry_used": False,
                "metadata": metadata,
            }
            return self.state.runtime_signal()

        record["count"] = int(record.get("count", 0)) + 1
        hypotheses = record.setdefault("hypotheses", set())
        changed_hypothesis = hypothesis_hash not in hypotheses
        hypotheses.add(hypothesis_hash)
        count = int(record["count"])

        if changed_hypothesis and not record.get("changed_retry_used"):
            record["changed_retry_used"] = True
            self.state.changed_hypothesis_retry = {
                "used": True,
                "count": count,
                "tool_name": tool_name,
                "signature": metadata,
            }
            self.state.last_signal = "changed_hypothesis_retry"
            return self.state.runtime_signal()

        reason = (
            "repeated_failure_changed_hypothesis_exhausted"
            if record.get("changed_retry_used")
            else "repeated_failure_same_hypothesis"
        )
        self.state.fallback_decision = {
            "action": "pause",
            "reason": reason,
            "count": count,
            "tool_name": tool_name,
            "signature": metadata,
        }
        self.state.last_signal = "fallback_decision"
        self.add_evidence(
            "fallback_decision",
            f"{tool_name}: {reason}",
            self.state.fallback_decision,
        )
        self._persist_resume_packet("repeated_failure_fallback", force=True)
        return self.state.runtime_signal()

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

    def persist_closure_packet(self, exit_reason: str = "final_response") -> Path | None:
        return self._persist_resume_packet(exit_reason, force=True)

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

    def _persist_resume_packet(self, reason: str, *, force: bool = False) -> Path | None:
        if self.persist_dir is None:
            return None
        key = reason or "threshold"
        if not force and key in self._persisted_reasons and self.state.resume_packet_path:
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
        "fallback_decision": state.fallback_decision,
        "changed_hypothesis_retry": state.changed_hypothesis_retry,
        "closure_contract": build_turn_closure_contract(state, exit_reason=reason),
        "latest_checkpoint": state.checkpoints[-1] if state.checkpoints else None,
        "evidence_ledger": [entry.to_dict() for entry in state.evidence_ledger],
        "role_handoffs": list(state.role_handoffs),
        "resume_instructions": (
            "Continue from the latest checkpoint, cite evidence entries used, "
            "and avoid repeating guardrail-triggering calls unchanged."
        ),
    }


def build_turn_closure_contract(
    state: LongTurnState,
    *,
    exit_reason: str | None,
) -> dict[str, Any]:
    """Return the explicit resume/closure contract for a long turn.

    This is deliberately machine-readable and conservative: only normal final
    assistant exits are marked closed.  Timeouts, interrupts, budget exhaustion,
    guardrail halts, and manual checkpoint packets are resumable and must not be
    represented to a user/operator as fully complete.
    """

    reason = exit_reason or "unknown"
    closed_reasons = {"final_response", "completed", "normal", "success"}
    closed_prefixes = ("text_response(",)
    resume_required = reason not in closed_reasons and not reason.startswith(closed_prefixes)
    latest_checkpoint = state.checkpoints[-1] if state.checkpoints else None
    next_action = None
    if isinstance(latest_checkpoint, dict):
        metadata = latest_checkpoint.get("metadata") or {}
        if isinstance(metadata, Mapping):
            raw_next = metadata.get("next_action") or metadata.get("action")
            if raw_next is not None:
                next_action = str(raw_next)

    return {
        "schema_version": 1,
        "status": "resume_required" if resume_required else "closed",
        "resume_required": resume_required,
        "exit_reason": reason,
        "user_visible_claim": "not_complete" if resume_required else "complete",
        "turn_id": state.turn_id,
        "session_id": state.session_id,
        "task_id": state.task_id,
        "resume_packet_path": state.resume_packet_path,
        "fallback_decision": state.fallback_decision,
        "changed_hypothesis_retry": state.changed_hypothesis_retry,
        "latest_checkpoint_label": latest_checkpoint.get("label") if isinstance(latest_checkpoint, dict) else None,
        "next_action": next_action,
    }


def should_render_action_relevant_checkpoint(
    signal: Mapping[str, Any] | None,
    *,
    turn_exit_reason: str | None = None,
) -> bool:
    """Return True only for checkpoints that change Marco's action/risk.

    Threshold-only long turns are intentionally quiet. Visible Discord/gateway
    checkpoints are reserved for interrupted/resumable turns, approval/blocker
    boundaries, or explicit action-required metadata.
    """

    data = dict(signal or {})
    reason = turn_exit_reason or ""
    if reason.startswith(("interrupted", "budget_exhausted", "guardrail_halt", "max_iterations_reached")):
        return True
    if data.get("action_required") or data.get("approval_required") or data.get("blocked"):
        return True
    if data.get("signal") in {"guardrail_halt", "approval_required", "blocked"}:
        return True
    contract = data.get("closure_contract")
    if isinstance(contract, Mapping) and contract.get("resume_required"):
        return True
    return False


def render_action_relevant_checkpoint(
    signal: Mapping[str, Any] | None,
    state: LongTurnState | None = None,
) -> str:
    """Render a terse gateway-safe checkpoint line.

    The gateway should show what changed and the next action, not local file
    paths or implementation internals.  Paths remain available in logs and the
    resume packet; chat surfaces get a profile-safe checkpoint breadcrumb.
    """

    data = dict(signal or {})
    api_calls = data.get("api_calls", getattr(state, "api_calls", 0) if state else 0)
    tool_calls = data.get("tool_calls", getattr(state, "tool_calls", 0) if state else 0)
    if "threshold_reasons" in data:
        reasons = data.get("threshold_reasons") or []
    elif state is not None:
        reasons = getattr(state, "threshold_reasons", []) or []
    else:
        reasons = []
    if isinstance(reasons, str):
        reasons_text = reasons
    else:
        reasons_text = ",".join(str(r) for r in (reasons or []))

    latest_checkpoint = state.checkpoints[-1] if state and state.checkpoints else None
    next_action = _checkpoint_next_action(latest_checkpoint)

    bits = ["⏱ Checkpoint saved", f"api={api_calls}", f"tools={tool_calls}"]
    if reasons_text:
        bits.append(f"threshold={reasons_text}")
    if next_action:
        bits.append(f"next: {next_action}")
    return " | ".join(bits)


def load_latest_resume_packet(persist_dir: Path | str, session_id: str) -> dict[str, Any] | None:
    """Load the newest resumable packet for a session, if one exists."""

    session_dir = Path(persist_dir) / _safe_component(session_id or "no-session")
    if not session_dir.exists() or not session_dir.is_dir():
        return None
    candidates: list[tuple[float, dict[str, Any]]] = []
    for path in session_dir.glob("*.json"):
        try:
            packet = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        contract = packet.get("closure_contract") if isinstance(packet, dict) else None
        if not isinstance(contract, Mapping) or not contract.get("resume_required"):
            continue
        created_at = packet.get("created_at")
        try:
            created = float(created_at)
        except (TypeError, ValueError):
            created = path.stat().st_mtime
        candidates.append((created, packet))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def render_resume_reanchor(packet: Mapping[str, Any] | None) -> str:
    """Render a compact, path-safe resume context from a packet."""

    if not isinstance(packet, Mapping):
        return ""
    contract = packet.get("closure_contract") or {}
    checkpoint = packet.get("latest_checkpoint") or {}
    evidence = packet.get("evidence_ledger") or []
    latest_label = checkpoint.get("label") if isinstance(checkpoint, Mapping) else None
    next_action = _checkpoint_next_action(checkpoint) if isinstance(checkpoint, Mapping) else None
    if not next_action and isinstance(contract, Mapping):
        raw_next = contract.get("next_action")
        next_action = str(raw_next) if raw_next else None
    evidence_bits: list[str] = []
    if isinstance(evidence, list):
        for entry in evidence[-3:]:
            if isinstance(entry, Mapping) and entry.get("summary"):
                evidence_bits.append(str(entry["summary"]))
    parts = ["Resume context from prior interrupted long turn"]
    if latest_label:
        parts.append(f"checkpoint: {latest_label}")
    if evidence_bits:
        parts.append("evidence: " + "; ".join(evidence_bits))
    if next_action:
        parts.append(f"next: {next_action}")
    return " | ".join(parts)


def _checkpoint_next_action(checkpoint: Mapping[str, Any] | None) -> str | None:
    if isinstance(checkpoint, Mapping):
        metadata = checkpoint.get("metadata") or {}
        if isinstance(metadata, Mapping):
            raw_next = metadata.get("next_action") or metadata.get("action")
            if raw_next is not None:
                return str(raw_next)
    return None


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


def _failure_signature(tool_name: str, args: Mapping[str, Any] | None, result: Any) -> tuple[str, dict[str, str]]:
    coerced_args = dict(args or {}) if isinstance(args, Mapping) else {}
    target_args = {
        key: value
        for key, value in coerced_args.items()
        if str(key).lower() not in {"hypothesis", "_hypothesis", "changed_hypothesis", "strategy", "rationale"}
    }
    hypothesis_payload = {
        key: value
        for key, value in coerced_args.items()
        if str(key).lower() in {"hypothesis", "_hypothesis", "changed_hypothesis", "strategy", "rationale"}
    }
    if not hypothesis_payload:
        hypothesis_payload = coerced_args
    target_hash = _hash_json(target_args)
    hypothesis_hash = _hash_json(hypothesis_payload)
    error_hash = _hash_json(_error_payload(result))
    metadata = {
        "tool_name": str(tool_name or ""),
        "target_hash": target_hash,
        "error_hash": error_hash,
        "hypothesis_hash": hypothesis_hash,
    }
    key = _hash_json({"tool_name": metadata["tool_name"], "target_hash": target_hash, "error_hash": error_hash})
    return key, metadata


def _error_payload(result: Any) -> Any:
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except Exception:
            return " ".join(result.strip().split())[:2000]
        if isinstance(parsed, Mapping):
            for key in ("error", "stderr", "exception", "message", "failed"):
                if key in parsed:
                    return {key: parsed.get(key)}
        return parsed
    return result


def _hash_json(value: Any) -> str:
    try:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        canonical = str(value)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
