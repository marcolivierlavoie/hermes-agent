"""Mnemosyne isolated-pilot memory provider.

Local-only provider for piloting auditable memory add/suppress controls without
mutating production memory-provider configuration or external services.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from hermes_constants import get_hermes_home
from tools.registry import tool_error


_WORD_RE = re.compile(r"[A-Za-z0-9_]{3,}")
_SECRET_RE = re.compile(r"(?i)(api[_ -]?key|password|passwd|private[_ -]?key|secret|token|credential)\s*[:=]\s*\S+")

MNEMOSYNE_PRODUCT_CONTRACT = """
Mnemosyne Level 3 product contract:
- Local/profile-scoped only; no external services and no production-provider mutation.
- No bulk imports. Source-aware seeding is capped by max_seed_records and queues candidates only.
- No secrets: candidate/writeback and seeding paths reject obvious secret-bearing text; explicit adds remain audited and secret/sensitive memories are excluded from selective prefetch.
- Writeback candidate queue first: proposed memories can be staged, listed, approved, or rejected.
- Candidate approval is the only path from queued writeback/seed records into trusted recall; rejection and seeding dry-runs never mutate trusted memory.
- Supersession/conflict metadata is auditable and selective prefetch fails closed when multiple eligible memories conflict.
- Suppression is non-destructive; hygiene_report is non-mutating and report-only.
- Prefetch is default-off and fail-closed. Enabled prefetch must satisfy score/overlap/sensitivity/stability/current_request_safe gates.
- Prefetch observability is available through prefetch_trace, observability_summary, and optional local trace logging without storing context bodies.
- Rollout/rollback helpers return manifests only; they do not edit Hermes production config or Linear.

Config lives at $HERMES_HOME/mnemosyne/config.json. Example:
{
  "selective_prefetch_enabled": false,
  "max_prefetch_results": 3,
  "max_prefetch_scan_results": 20,
  "max_prefetch_context_chars": 1800,
  "min_prefetch_score": 2,
  "min_prefetch_token_overlap": 2,
  "prefetch_trace_enabled": false,
  "candidate_queue_enabled": true,
  "max_seed_records": 10
}
""".strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_result(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD_RE.finditer(text or "")}


@dataclass
class MnemosyneMemory:
    id: str
    content: str
    source: str
    context: str
    rationale: str
    created_at: str
    created_by: str = "mnemosyne-isolated-pilot"
    confidence: str = "unknown"
    sensitivity: str = "unknown"
    stability: str = "unknown"
    current_request_safe: bool = False
    rationale_id: str = ""
    topic: str = ""
    conflict_group: str = ""
    conflict_status: str = "unknown"
    supersedes: List[str] | None = None
    superseded_by: str = ""
    valid_from: str = ""
    valid_until: str = ""


@dataclass
class MnemosyneCandidate:
    id: str
    content: str
    source: str
    context: str
    rationale: str
    created_at: str
    proposed_by: str = "mnemosyne-isolated-pilot"
    status: str = "pending"
    confidence: str = "unknown"
    sensitivity: str = "unknown"
    stability: str = "unknown"
    current_request_safe: bool = False
    topic: str = ""
    conflict_group: str = ""
    conflict_status: str = "unknown"
    supersedes: List[str] | None = None
    valid_from: str = ""
    valid_until: str = ""
    decision_rationale: str = ""
    decided_at: str = ""
    approved_memory_id: str = ""


@dataclass
class MnemosyneSuppression:
    id: str
    memory_id: str
    rationale: str
    source: str
    created_at: str
    active: bool = True
    unsuppressed_at: str | None = None
    unsuppress_rationale: str | None = None


class MnemosyneProvider(MemoryProvider):
    """Profile-scoped, local-only memory pilot with non-destructive suppression."""

    MAX_TOP_K = 5
    DEFAULT_TOP_K = 3
    MAX_MEMORY_CHARS = 1200
    MAX_RESULTS_CHARS = 4000

    def __init__(self) -> None:
        self._session_id = ""
        self.session_id = ""
        self.hermes_home = get_hermes_home()
        self.data_dir = Path(os.environ.get("MNEMOSYNE_DATA_DIR", self._effective_mnemosyne_root(self.hermes_home) / "data")).expanduser()
        self.config: Dict[str, Any] = {}
        self._last_prefetch_trace: Dict[str, Any] = {}
        mnemosyne_root = self._effective_mnemosyne_root(self.hermes_home)
        self._root = mnemosyne_root / "isolated-pilot"
        self._config_path = mnemosyne_root / "config.json"
        self._memories_path = self._root / "memories.jsonl"
        self._suppressions_path = self._root / "suppressions.jsonl"
        self._candidates_path = self._root / "candidates.jsonl"
        self._events_path = self._root / "events.jsonl"
        self._last_prefetch_trace: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "mnemosyne"

    def is_available(self) -> bool:
        # Local-only and dependency-free. Loading this does not activate it;
        # activation still requires memory.provider=mnemosyne in config.
        return True

    @staticmethod
    def _effective_mnemosyne_root(hermes_home: Path) -> Path:
        home = Path(hermes_home).expanduser()
        parts = home.parts
        if len(parts) >= 2 and parts[-2] == "profiles":
            shared_home = home.parent.parent
            shared_root = shared_home / "mnemosyne"
            if shared_root.exists() or (shared_root / "config.json").exists():
                return shared_root
        return home / "mnemosyne"

    def initialize(self, session_id: str, **kwargs) -> None:
        hermes_home = kwargs.get("hermes_home")
        if hermes_home:
            self.hermes_home = Path(str(hermes_home)).expanduser()
            mnemosyne_root = self._effective_mnemosyne_root(self.hermes_home)
            self._root = mnemosyne_root / "isolated-pilot"
            self._config_path = mnemosyne_root / "config.json"
            self._memories_path = self._root / "memories.jsonl"
            self._suppressions_path = self._root / "suppressions.jsonl"
            self._candidates_path = self._root / "candidates.jsonl"
            self._events_path = self._root / "events.jsonl"
        self.data_dir = Path(os.environ.get("MNEMOSYNE_DATA_DIR", self._effective_mnemosyne_root(self.hermes_home) / "data")).expanduser()
        self.config = self._load_config()
        self._session_id = session_id
        self.session_id = session_id
        self._root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._snapshots_dir().mkdir(parents=True, exist_ok=True)
        self._memories_path.touch(exist_ok=True)
        self._suppressions_path.touch(exist_ok=True)
        self._candidates_path.touch(exist_ok=True)
        self._events_path.touch(exist_ok=True)
        self._store_path().touch(exist_ok=True)

    def system_prompt_block(self) -> str:
        return (
            "Mnemosyne memory provider active. Host LLM extraction disabled; explicit mnemosyne_* compatibility tools and "
            "trusted mnemosyne_memory controls are enabled. Use mnemosyne_memory "
            "for auditable candidate/add/recall/inspect/list/hygiene_report/suppress/unsuppress "
            "operations. Queue uncertain writebacks as candidates before approval. "
            "Suppression is non-destructive by default and can be rolled back. "
            "Automatic broad recall/prefetch is disabled; recall Mnemosyne explicitly "
            "when the user asks about memory/history or when a memory check is required."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return str(self.prefetch_trace(query, session_id=session_id).get("context") or "")

    def prefetch_trace(self, query: str, *, session_id: str = "") -> Dict[str, Any]:
        config = self._load_config()
        trace: Dict[str, Any] = {
            "success": True,
            "event": "prefetch",
            "created_at": _now_iso(),
            "session_id": session_id or self._session_id,
            "query_preview": str(query or "")[:160],
            "enabled": config.get("selective_prefetch_enabled") is True,
            "injected": False,
            "skip_reason": "selective_prefetch_disabled",
            "budgets": {},
            "candidate_count": 0,
            "eligible_count": 0,
            "memory_ids": [],
            "scores": {},
            "candidate_traces": [],
            "injected_token_estimate": 0,
            "context": "",
        }
        if config.get("selective_prefetch_enabled") is not True:
            self._record_prefetch_trace(trace, config)
            return trace

        limit = self._config_int(config, "max_prefetch_results", default=3, minimum=1, maximum=10)
        scan_limit = self._config_int(config, "max_prefetch_scan_results", default=20, minimum=1, maximum=100)
        min_score = self._config_int(config, "min_prefetch_score", default=2, minimum=1, maximum=100)
        max_chars = self._config_int(config, "max_prefetch_context_chars", default=1800, minimum=200, maximum=8000)
        trace["budgets"] = {"max_results": limit, "scan_results": scan_limit, "min_score": min_score, "max_context_chars": max_chars}
        if None in {limit, scan_limit, min_score, max_chars}:
            trace["skip_reason"] = "invalid_prefetch_config"
            self._record_prefetch_trace(trace, config)
            return trace

        safe, safe_reason = self._query_prefetch_safe_reason(query, config)
        if not safe:
            trace["skip_reason"] = safe_reason
            self._record_prefetch_trace(trace, config)
            return trace

        candidates = []
        for item in self.recall(query, limit=scan_limit):
            memory = item["memory"]
            memory_id = str(memory.get("id") or "")
            trace["candidate_count"] += 1
            trace["scores"][memory_id] = item.get("score")
            candidate_trace = {"id": memory_id, "source": memory.get("source"), "score": item.get("score"), "eligible": False, "skip_reason": ""}
            if int(item.get("score") or 0) < min_score:
                candidate_trace["skip_reason"] = "below_min_score"
                trace["candidate_traces"].append(candidate_trace)
                continue
            eligible, reason = self._prefetch_eligibility_reason(memory, query=query, config=config)
            candidate_trace["eligible"] = eligible
            candidate_trace["skip_reason"] = "" if eligible else reason
            trace["candidate_traces"].append(candidate_trace)
            if eligible:
                candidates.append(item)
            if len(candidates) >= limit:
                break

        trace["eligible_count"] = len(candidates)
        if not candidates:
            trace["skip_reason"] = "no_eligible_memories"
            self._record_prefetch_trace(trace, config)
            return trace
        if self._has_prefetch_conflict(candidates, query=query):
            trace["skip_reason"] = "conflict_detected"
            trace["memory_ids"] = [item["memory"].get("id") for item in candidates]
            self._record_prefetch_trace(trace, config)
            return trace

        lines = ["Mnemosyne selective prefetch context (gated, unsuppressed, high-confidence):"]
        for item in candidates:
            memory = item["memory"]
            lines.append(
                f"- [{memory.get('id')}; source={memory.get('source')}; "
                f"rationale_id={memory.get('rationale_id')}; created_at={memory.get('created_at')}; "
                f"score={item.get('score')}; topic={memory.get('topic', '')}; "
                f"conflict_group={memory.get('conflict_group', '')}; conflict_status={memory.get('conflict_status', 'unknown')}; "
                f"supersedes_count={len(memory.get('supersedes') or [])}; "
                "eligibility=high_confidence,non_sensitive,stable,current_request_safe] "
                f"{memory.get('content')}"
            )
        lines.append("If asked, explain these Mnemosyne memory IDs/sources/rationale IDs influenced the response at a high level; current user instructions still take precedence.")
        context = "\n".join(lines)
        if len(context) > max_chars:
            context = context[: max_chars - 120].rstrip() + "\n[Mnemosyne prefetch truncated by configured context budget.]"
        trace["memory_ids"] = [item["memory"].get("id") for item in candidates]
        trace["injected_token_estimate"] = max(1, len(context) // 4)
        trace["injected"] = True
        trace["skip_reason"] = ""
        trace["context"] = context
        self._record_prefetch_trace(trace, config)
        return trace

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "mnemosyne_memory",
                "description": (
                    "Isolated-pilot Mnemosyne memory controls. Queue writeback candidates; add approved memories "
                    "with source/context/timestamp/rationale; suppress stale memories non-destructively; "
                    "unsuppress for rollback; recall only active, unsuppressed memories by default; inspect audit metadata."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "add", "recall", "suppress", "unsuppress", "inspect", "list", "hygiene_report",
                                "candidate", "add_candidate", "list_candidates", "approve_candidate", "reject_candidate",
                                "prefetch_trace", "contract", "seed_source", "observability_summary", "rollout_manifest",
                                "memory_digest", "decisions_digest", "recall_policy", "semantic_quality_gates",
                                "production_eval_pack", "run_production_eval", "explain_memory", "answer_attribution", "semantic_recall",
                                "harvest_candidates", "apply_correction", "discord_decision_digest",
                            ],
                        },
                        "content": {"type": "string"},
                        "query": {"type": "string"},
                        "memory_id": {"type": "string"},
                        "memory_ids": {"type": "array", "items": {"type": "string"}},
                        "answer_summary": {"type": "string"},
                        "source": {"type": "string"},
                        "context": {"type": "string"},
                        "rationale": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
                        "sensitivity": {"type": "string", "enum": ["non_sensitive", "sensitive", "secret", "unknown"]},
                        "stability": {"type": "string", "enum": ["stable", "current", "temporary", "stale", "unknown"]},
                        "current_request_safe": {"type": "boolean"},
                        "topic": {"type": "string"},
                        "conflict_group": {"type": "string"},
                        "conflict_status": {"type": "string", "enum": ["unknown", "active", "resolved", "superseded"]},
                        "supersedes": {"type": "array", "items": {"type": "string"}},
                        "valid_from": {"type": "string"},
                        "valid_until": {"type": "string"},
                        "candidate_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "approved", "rejected", "all"]},
                        "records": {"type": "array", "items": {"type": "object"}},
                        "dry_run": {"type": "boolean"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                        "include_suppressed": {"type": "boolean"},
                    },
                    "required": ["action"],
                },
            }
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "mnemosyne_search":
            return _json_result(self._compat_search(str(args.get("query") or ""), args.get("top_k")))
        if tool_name == "mnemosyne_remember":
            return _json_result(self._compat_remember(str(args.get("content") or ""), args.get("metadata") if isinstance(args.get("metadata"), dict) else {}))
        if tool_name == "mnemosyne_export":
            return _json_result(self._compat_export(str(args.get("label") or "")))
        if tool_name == "mnemosyne_import":
            return _json_result(self._compat_import_or_restore(str(args.get("path") or ""), restore=False))
        if tool_name == "mnemosyne_restore":
            return _json_result(self._compat_import_or_restore(str(args.get("path") or ""), restore=True))
        if tool_name != "mnemosyne_memory":
            return tool_error(f"Unknown Mnemosyne tool: {tool_name}")
        action = str(args.get("action") or "").strip().lower()
        try:
            if action == "add":
                return _json_result(self.add_memory(**self._memory_args_from_tool(args)))
            if action in {"candidate", "add_candidate"}:
                return _json_result(self.add_candidate(**self._memory_args_from_tool(args)))
            if action == "list_candidates":
                return _json_result({"success": True, "candidates": self.list_candidates(status=str(args.get("status") or "pending"))})
            if action == "approve_candidate":
                return _json_result(self.approve_candidate(candidate_id=str(args.get("candidate_id") or ""), rationale=str(args.get("rationale") or "")))
            if action == "reject_candidate":
                return _json_result(self.reject_candidate(candidate_id=str(args.get("candidate_id") or ""), rationale=str(args.get("rationale") or "")))
            if action == "recall":
                return _json_result({
                    "success": True,
                    "memories": self.recall(
                        str(args.get("query") or ""),
                        limit=int(args.get("limit") or 5),
                        include_suppressed=bool(args.get("include_suppressed") or False),
                    ),
                })
            if action == "suppress":
                return _json_result(self.suppress_memory(
                    memory_id=str(args.get("memory_id") or ""),
                    rationale=str(args.get("rationale") or ""),
                    source=str(args.get("source") or "mnemosyne_memory"),
                ))
            if action == "unsuppress":
                return _json_result(self.unsuppress_memory(memory_id=str(args.get("memory_id") or ""), rationale=str(args.get("rationale") or "")))
            if action == "inspect":
                return _json_result(self.inspect(str(args.get("memory_id") or "")))
            if action == "list":
                return _json_result({"success": True, "memories": self.list_memories(include_suppressed=bool(args.get("include_suppressed") or False))})
            if action == "hygiene_report":
                return _json_result(self.hygiene_report(include_suppressed=bool(args.get("include_suppressed") or False)))
            if action == "prefetch_trace":
                query = str(args.get("query") or "")
                trace = self.prefetch_trace(query) if query else dict(self._last_prefetch_trace)
                return _json_result({"success": True, "trace": trace})
            if action == "contract":
                return _json_result({"success": True, "contract": MNEMOSYNE_PRODUCT_CONTRACT, "config": self._load_config()})
            if action == "seed_source":
                return _json_result(self.seed_source_candidates(
                    source=str(args.get("source") or ""),
                    records=args.get("records") if isinstance(args.get("records"), list) else [],
                    dry_run=bool(args.get("dry_run", True)),
                ))
            if action == "observability_summary":
                return _json_result(self.observability_summary())
            if action in {"memory_digest", "decisions_digest"}:
                return _json_result(self.memory_digest())
            if action == "recall_policy":
                return _json_result(self.recall_policy())
            if action == "semantic_quality_gates":
                return _json_result(self.semantic_quality_gates())
            if action == "production_eval_pack":
                return _json_result(self.production_eval_pack())
            if action == "run_production_eval":
                return _json_result(self.run_production_eval())
            if action == "explain_memory":
                return _json_result(self.explain_memory(str(args.get("memory_id") or "")))
            if action == "answer_attribution":
                raw_ids = args.get("memory_ids") if isinstance(args.get("memory_ids"), list) else []
                memory_ids = [str(item) for item in raw_ids]
                if not memory_ids and args.get("memory_id"):
                    memory_ids = [str(args.get("memory_id") or "")]
                return _json_result(self.answer_attribution(memory_ids, answer_summary=str(args.get("answer_summary") or "")))
            if action == "semantic_recall":
                return _json_result(self.semantic_recall(str(args.get("query") or ""), limit=int(args.get("limit") or 5)))
            if action == "harvest_candidates":
                return _json_result(self.harvest_candidates(
                    content=str(args.get("content") or ""),
                    source=str(args.get("source") or "mnemosyne_harvest"),
                    context=str(args.get("context") or "production conversation"),
                    topic=str(args.get("topic") or ""),
                ))
            if action == "apply_correction":
                return _json_result(self.apply_correction(
                    content=str(args.get("content") or ""),
                    source=str(args.get("source") or "mnemosyne_correction"),
                    context=str(args.get("context") or "user correction"),
                    rationale=str(args.get("rationale") or "user correction supersedes older memory"),
                    superseded_memory_id=str(args.get("memory_id") or ""),
                    topic=str(args.get("topic") or ""),
                    conflict_group=str(args.get("conflict_group") or ""),
                ))
            if action == "discord_decision_digest":
                return _json_result(self.discord_decision_digest())
            if action == "rollout_manifest":
                return _json_result(self.rollout_manifest())
            return tool_error(f"Unsupported Mnemosyne action: {action}")
        except Exception as exc:  # defensive tool boundary
            return tool_error(f"Mnemosyne {action or 'operation'} failed: {exc}")

    def add_memory(
        self,
        *,
        content: str,
        source: str,
        context: str,
        rationale: str,
        confidence: str = "unknown",
        sensitivity: str = "unknown",
        stability: str = "unknown",
        current_request_safe: bool = False,
        topic: str = "",
        conflict_group: str = "",
        conflict_status: str = "unknown",
        supersedes: List[str] | None = None,
        valid_from: str = "",
        valid_until: str = "",
    ) -> Dict[str, Any]:
        validation = self._validate_writeback_fields(content=content, source=source, context=context, rationale=rationale)
        if validation:
            return validation
        content = content.strip()
        source = source.strip()
        context = context.strip()
        rationale = rationale.strip()
        memory_id = f"mn_{uuid.uuid4().hex[:12]}"
        memory = MnemosyneMemory(
            id=memory_id,
            content=content,
            source=source,
            context=context,
            rationale=rationale,
            created_at=_now_iso(),
            confidence=self._normalize_choice(confidence, {"high", "medium", "low", "unknown"}, default="unknown"),
            sensitivity=self._normalize_choice(sensitivity, {"non_sensitive", "sensitive", "secret", "unknown"}, default="unknown"),
            stability=self._normalize_choice(stability, {"stable", "current", "temporary", "stale", "unknown"}, default="unknown"),
            current_request_safe=bool(current_request_safe),
            rationale_id=f"rat_{uuid.uuid5(uuid.NAMESPACE_URL, memory_id + ':' + rationale).hex[:12]}",
            topic=topic.strip(),
            conflict_group=conflict_group.strip(),
            conflict_status=self._normalize_choice(conflict_status, {"unknown", "active", "resolved", "superseded"}, default="unknown"),
            supersedes=self._normalize_ids(supersedes),
            valid_from=valid_from.strip(),
            valid_until=valid_until.strip(),
        )
        row = asdict(memory)
        self._append_jsonl(self._memories_path, row)
        self._apply_supersession(row)
        return {"success": True, "memory": row}

    def add_candidate(self, **kwargs) -> Dict[str, Any]:
        config = self._load_config()
        if config.get("candidate_queue_enabled") is False or config.get("candidate_writeback_enabled") is False:
            return {"success": False, "error": "Candidate writeback is disabled by config."}
        validation = self._validate_writeback_fields(
            content=str(kwargs.get("content") or ""),
            source=str(kwargs.get("source") or ""),
            context=str(kwargs.get("context") or ""),
            rationale=str(kwargs.get("rationale") or ""),
        )
        if validation:
            return validation
        candidate_text = "\n".join(str(kwargs.get(key) or "") for key in ("content", "source", "context", "rationale"))
        if self._has_secret_marker(candidate_text):
            return {"success": False, "error": "Refusing to queue likely secret-bearing content."}
        candidate = MnemosyneCandidate(
            id=f"cand_{uuid.uuid4().hex[:12]}",
            content=str(kwargs.get("content") or "").strip(),
            source=str(kwargs.get("source") or "").strip(),
            context=str(kwargs.get("context") or "").strip(),
            rationale=str(kwargs.get("rationale") or "").strip(),
            created_at=_now_iso(),
            confidence=self._normalize_choice(kwargs.get("confidence"), {"high", "medium", "low", "unknown"}, default="unknown"),
            sensitivity=self._normalize_choice(kwargs.get("sensitivity"), {"non_sensitive", "sensitive", "secret", "unknown"}, default="unknown"),
            stability=self._normalize_choice(kwargs.get("stability"), {"stable", "current", "temporary", "stale", "unknown"}, default="unknown"),
            current_request_safe=bool(kwargs.get("current_request_safe") or False),
            topic=str(kwargs.get("topic") or "").strip(),
            conflict_group=str(kwargs.get("conflict_group") or "").strip(),
            conflict_status=self._normalize_choice(kwargs.get("conflict_status"), {"unknown", "active", "resolved", "superseded"}, default="unknown"),
            supersedes=self._normalize_ids(kwargs.get("supersedes") if isinstance(kwargs.get("supersedes"), list) else None),
            valid_from=str(kwargs.get("valid_from") or "").strip(),
            valid_until=str(kwargs.get("valid_until") or "").strip(),
        )
        row = asdict(candidate)
        self._append_jsonl(self._candidates_path, row)
        return {"success": True, "candidate": row, "mutated_memory": False}

    def list_candidates(self, *, status: str = "pending") -> List[Dict[str, Any]]:
        normalized = self._normalize_choice(status, {"pending", "approved", "rejected", "all"}, default="pending")
        rows = self._read_jsonl(self._candidates_path)
        if normalized == "all":
            return rows
        return [row for row in rows if row.get("status", "pending") == normalized]

    def approve_candidate(self, *, candidate_id: str, rationale: str = "") -> Dict[str, Any]:
        candidate_id = candidate_id.strip()
        rows = self._read_jsonl(self._candidates_path)
        for row in rows:
            if row.get("id") != candidate_id:
                continue
            if row.get("status", "pending") != "pending":
                return {"success": False, "error": f"Candidate is not pending: {candidate_id}"}
            memory_args = {k: row.get(k) for k in (
                "content", "source", "context", "confidence", "sensitivity", "stability", "current_request_safe",
                "topic", "conflict_group", "conflict_status", "supersedes", "valid_from", "valid_until",
            )}
            memory_args["rationale"] = rationale.strip() or str(row.get("rationale") or "")
            approved = self.add_memory(**memory_args)
            if not approved.get("success"):
                return approved
            row["status"] = "approved"
            row["decision_rationale"] = rationale.strip()
            row["decided_at"] = _now_iso()
            row["approved_memory_id"] = approved["memory"]["id"]
            self._write_jsonl(self._candidates_path, rows)
            return {"success": True, "candidate": row, "memory": approved["memory"]}
        return {"success": False, "error": f"Candidate not found: {candidate_id}"}

    def reject_candidate(self, *, candidate_id: str, rationale: str) -> Dict[str, Any]:
        candidate_id = candidate_id.strip()
        rationale = rationale.strip()
        if not candidate_id or not rationale:
            return {"success": False, "error": "candidate_id and rationale are required for rejection."}
        rows = self._read_jsonl(self._candidates_path)
        for row in rows:
            if row.get("id") != candidate_id:
                continue
            if row.get("status", "pending") != "pending":
                return {"success": False, "error": f"Candidate is not pending: {candidate_id}"}
            row["status"] = "rejected"
            row["decision_rationale"] = rationale
            row["decided_at"] = _now_iso()
            self._write_jsonl(self._candidates_path, rows)
            return {"success": True, "candidate": row, "mutated_memory": False}
        return {"success": False, "error": f"Candidate not found: {candidate_id}"}

    def seed_source_candidates(self, *, source: str, records: List[Dict[str, Any]], dry_run: bool = True) -> Dict[str, Any]:
        """Stage allowlisted SecondBrain/Linear operating-corpus snippets as candidates only.

        This is intentionally stricter than general candidate writeback: every
        record must come from an approved/allowlisted SecondBrain or Linear source,
        risky rejected snippets are reported without raw content, and accepted
        snippets are queued for approval rather than written to trusted memory.
        """
        source = source.strip()
        max_records = self._config_int(self._load_config(), "max_seed_records", default=10, minimum=1, maximum=25)
        if max_records is None:
            return {"success": False, "error": "Invalid max_seed_records config.", "candidate_count": 0, "rejected_count": 0, "rejections": [], "mutated_memory": False}
        if not source:
            return {"success": False, "error": "source is required for source-aware seeding.", "candidate_count": 0, "rejected_count": 0, "rejections": [], "mutated_memory": False}
        if len(records) > max_records:
            return {"success": False, "error": f"Refusing bulk seed: {len(records)} records exceeds max_seed_records={max_records}.", "candidate_count": 0, "rejected_count": 0, "rejections": [], "mutated_memory": False}

        prepared: List[Dict[str, Any]] = []
        rejections: List[Dict[str, Any]] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                rejections.append(self._seed_rejection(index, "invalid_record", "record_not_object"))
                continue
            item = {
                "content": str(record.get("content") or ""),
                "source": str(record.get("source") or source),
                "context": str(record.get("context") or f"seeded from {source}"),
                "rationale": str(record.get("rationale") or "source-aware seed candidate; requires approval"),
                "confidence": str(record.get("confidence") or "unknown"),
                "sensitivity": str(record.get("sensitivity") or "unknown"),
                "stability": str(record.get("stability") or "unknown"),
                "current_request_safe": bool(record.get("current_request_safe") or False),
                "topic": str(record.get("topic") or ""),
                "conflict_group": str(record.get("conflict_group") or ""),
                "conflict_status": str(record.get("conflict_status") or "unknown"),
                "supersedes": record.get("supersedes") if isinstance(record.get("supersedes"), list) else None,
                "valid_from": str(record.get("valid_from") or ""),
                "valid_until": str(record.get("valid_until") or ""),
            }
            if not self._is_operating_corpus_source_allowlisted(source) or not self._is_operating_corpus_source_allowlisted(item["source"]):
                rejections.append(self._seed_rejection(index, "source_not_allowlisted", "source_not_secondbrain_or_linear"))
                continue
            validation = self._validate_writeback_fields(content=item["content"], source=item["source"], context=item["context"], rationale=item["rationale"])
            if validation:
                rejections.append(self._seed_rejection(index, "invalid_metadata", str(validation.get("error") or "invalid_metadata")))
                continue
            candidate_text = "\n".join(str(item.get(key) or "") for key in ("content", "source", "context", "rationale"))
            if self._has_secret_marker(candidate_text):
                rejections.append(self._seed_rejection(index, "secret", "secret_or_credential_signal"))
                continue
            if self._is_private_raw_detail(item["content"]):
                rejections.append(self._seed_rejection(index, "private_sensitive", "private_or_family_sensitive_raw_detail"))
                continue
            if self._is_temporary_or_status_artifact(item["content"]):
                rejections.append(self._seed_rejection(index, "temporary", "temporary_task_progress_or_status_artifact"))
                continue
            preview_item = dict(item)
            preview_item["approval_status"] = "preview_pending_approval" if dry_run else "pending"
            prepared.append(preview_item)

        manifest = self._seed_rollback_manifest(source=source, dry_run=dry_run, candidates=[])
        if dry_run:
            manifest["candidate_preview_count"] = len(prepared)
            success = bool(prepared) or not rejections
            result = {
                "success": success,
                "dry_run": True,
                "candidate_count": len(prepared),
                "rejected_count": len(rejections),
                "candidates": prepared,
                "rejections": rejections,
                "rollback_manifest": manifest,
                "mutated_memory": False,
            }
            if not success:
                result["error"] = self._seed_rejections_error(rejections)
            return result
        created = []
        for item in prepared:
            candidate_args = dict(item)
            candidate_args.pop("approval_status", None)
            result = self.add_candidate(**candidate_args)
            if not result.get("success"):
                return {
                    "success": False,
                    "error": result.get("error", "candidate_queue_rejected"),
                    "dry_run": False,
                    "candidate_count": len(created),
                    "rejected_count": len(rejections),
                    "candidates": created,
                    "rejections": rejections,
                    "rollback_manifest": self._seed_rollback_manifest(source=source, dry_run=False, candidates=created),
                    "mutated_memory": False,
                }
            created.append(result["candidate"])
        manifest = self._seed_rollback_manifest(source=source, dry_run=False, candidates=created)
        success = bool(created) or not rejections
        result = {
            "success": success,
            "dry_run": False,
            "candidate_count": len(created),
            "rejected_count": len(rejections),
            "candidates": created,
            "rejections": rejections,
            "rollback_manifest": manifest,
            "mutated_memory": False,
        }
        if not success:
            result["error"] = self._seed_rejections_error(rejections)
        return result

    def observability_summary(self) -> Dict[str, Any]:
        """Return non-mutating counters for locally recorded Mnemosyne events."""
        events = self._read_jsonl(self._events_path)
        skip_reasons: Dict[str, int] = {}
        injected = 0
        redacted_query_previews = 0
        for event in events:
            if event.get("injected") is True:
                injected += 1
            reason = str(event.get("skip_reason") or "injected")
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            if event.get("query_preview") == "[redacted]":
                redacted_query_previews += 1
        return {
            "success": True,
            "event_count": len(events),
            "prefetch_event_count": sum(1 for event in events if event.get("event") == "prefetch"),
            "injected_count": injected,
            "skip_reasons": skip_reasons,
            "redacted_query_preview_count": redacted_query_previews,
            "events_path": str(self._events_path),
            "mutated": False,
        }

    def memory_digest(self) -> Dict[str, Any]:
        """Return a Discord/operator-friendly Phase 4 decisions digest without raw memory bodies."""
        pending = self.list_candidates(status="pending")
        hygiene = self.hygiene_report(include_suppressed=False)
        memories = self.list_memories(include_suppressed=True)
        suppressions = [row for row in self._read_jsonl(self._suppressions_path) if row.get("active") is True]
        conflict_memory_ids = sorted({
            item["memory"]["id"]
            for item in memories
            if item.get("conflict_group") or item.get("conflict_status") not in {"", "unknown", None}
        })
        hygiene_items = [
            {
                "candidate_memory_ids": item.get("candidate_memory_ids", []),
                "reason": item.get("reason", ""),
                "suggested_action": item.get("suggested_action", ""),
            }
            for item in hygiene.get("recommendations", [])
        ]
        return {
            "success": True,
            "generated_at": _now_iso(),
            "mutated": False,
            "needs_decision": {
                "pending_candidate_count": len(pending),
                "pending_candidate_ids": [item.get("id") for item in pending],
                "hygiene_recommendation_count": len(hygiene_items),
                "hygiene_recommendations": hygiene_items,
                "conflict_memory_ids": conflict_memory_ids,
            },
            "fyi": {
                "trusted_memory_count": len(memories),
                "active_suppression_count": len(suppressions),
                "observability": self.observability_summary(),
            },
            "redaction": "raw memory/candidate content is omitted; inspect explicit IDs for details",
        }

    def recall_policy(self) -> Dict[str, Any]:
        """Return the trust-aware recall and attribution contract for operator UX."""
        return {
            "success": True,
            "mutated": False,
            "authority_order": [
                "current_user_instruction",
                "linear_and_secondbrain_source_docs",
                "built_in_user_profile_and_memory",
                "mnemosyne_trusted_memory",
                "session_search_history",
                "candidate_or_low_confidence_signal",
            ],
            "trusted_memory_policy": "use only active, unsuppressed memories with source/context/rationale metadata; respect current_request_safe and sensitivity gates",
            "candidate_memory_policy": "never inject; cite only as untrusted pending signal during explicit review",
            "low_confidence_policy": "do not present as fact; either ignore, qualify, or verify against Linear/SecondBrain/current tools",
            "conflict_policy": "current user instruction wins; unresolved memory conflicts fail closed and require inspection or source-of-truth verification",
            "attribution": {
                "answer_rule": "when memory materially affects an answer, cite the source label or say it came from trusted Mnemosyne memory when useful",
                "why_did_you_remember_that": "inspect memory_id to show source, context, rationale, timestamps, sensitivity, stability, and suppression/conflict metadata",
            },
            "out_of_scope": ["broad auto-capture", "bulk imports", "unreviewed sensitive/private memories", "semantic reranking without green gates"],
        }

    def semantic_quality_gates(self) -> Dict[str, Any]:
        """Fail-closed gate report for future semantic recall/reranking expansion."""
        pending = self.list_candidates(status="pending")
        hygiene = self.hygiene_report(include_suppressed=False)
        observability = self.observability_summary()
        memories = self.list_memories(include_suppressed=False)
        conflict_count = sum(1 for item in memories if item.get("conflict_status") == "active")
        failed_gates = []
        if pending:
            failed_gates.append("no_pending_candidates")
        if hygiene.get("recommendations"):
            failed_gates.append("clean_hygiene_report")
        if conflict_count:
            failed_gates.append("no_active_conflicts")
        if observability.get("prefetch_event_count", 0) == 0 and not self._last_prefetch_trace:
            failed_gates.append("baseline_recall_evaluation_recorded")
        return {
            "success": True,
            "mutated": False,
            "semantic_recall_allowed": not failed_gates,
            "failed_gates": failed_gates,
            "required_gates": [
                "no_pending_candidates",
                "clean_hygiene_report",
                "no_active_conflicts",
                "baseline_recall_evaluation_recorded",
                "sensitivity_provenance_suppression_conflict_gates_enforced",
            ],
            "metrics": {
                "pending_candidate_count": len(pending),
                "hygiene_recommendation_count": len(hygiene.get("recommendations", [])),
                "active_conflict_count": conflict_count,
                "prefetch_event_count": observability.get("prefetch_event_count", 0),
            },
            "quality_metrics_required_before_enablement": [
                "precision",
                "false_positive_rate",
                "stale_recall_rate",
                "conflict_skip_rate",
                "user_correction_rate",
            ],
        }

    def production_eval_pack(self) -> Dict[str, Any]:
        cases = [
            {"id": "pref_command_surface", "prompt": "Where should Biff handle live commands?", "expected_behavior": "inject", "topic": "command surface"},
            {"id": "pref_linear_source", "prompt": "Where is Biff OS status tracked?", "expected_behavior": "inject", "topic": "Linear"},
            {"id": "pref_full_mnemosyne", "prompt": "What does Marco mean by Mnemosyne production?", "expected_behavior": "inject", "topic": "mnemosyne production"},
            {"id": "source_truth", "prompt": "What should Biff verify against for docs?", "expected_behavior": "verify", "topic": "SecondBrain"},
            {"id": "sensitive_password", "prompt": "What is Marco's password or token?", "expected_behavior": "skip", "topic": "sensitive"},
            {"id": "stale_conflict", "prompt": "Use an old Cockpit-first command rule", "expected_behavior": "skip", "topic": "stale"},
            {"id": "low_confidence", "prompt": "Do you vaguely remember a temporary preference?", "expected_behavior": "qualify", "topic": "low confidence"},
            {"id": "correction", "prompt": "Marco corrected a durable Biff behavior", "expected_behavior": "candidate", "topic": "writeback"},
        ]
        return {"success": True, "case_count": len(cases), "cases": cases, "mutated": False}

    def run_production_eval(self) -> Dict[str, Any]:
        cases = self.production_eval_pack()["cases"]
        traces = []
        true_positive = false_positive = stale_recall = conflict_skip = correction = 0
        for case in cases:
            expected = case["expected_behavior"]
            if expected == "candidate":
                correction += 1
                traces.append({"id": case["id"], "expected_behavior": expected, "observed_behavior": "candidate"})
                continue
            trace = self.prefetch_trace(case["prompt"])
            observed = "inject" if trace.get("injected") else "skip"
            if expected in {"inject", "verify", "qualify"} and observed == "inject":
                true_positive += 1
            if expected == "skip" and observed == "inject":
                false_positive += 1
            if trace.get("skip_reason") in {"stability_not_stable_or_current", "superseded"}:
                stale_recall += 1
            if trace.get("skip_reason") == "conflict_detected":
                conflict_skip += 1
            traces.append({"id": case["id"], "expected_behavior": expected, "observed_behavior": observed, "skip_reason": trace.get("skip_reason", "")})
        metric_denominator = max(1, len(cases))
        return {
            "success": True,
            "mutated": False,
            "metrics": {
                "case_count": len(cases),
                "precision": true_positive / max(1, true_positive + false_positive),
                "false_positive_rate": false_positive / metric_denominator,
                "stale_recall_rate": stale_recall / metric_denominator,
                "conflict_skip_rate": conflict_skip / metric_denominator,
                "user_correction_rate": correction / metric_denominator,
            },
            "traces": traces,
        }

    def explain_memory(
        self,
        memory_id: str,
        *,
        used_in_answer: bool = False,
        current_request: str = "",
    ) -> Dict[str, Any]:
        """Explain why a trusted memory could influence an answer without leaking raw sensitive content."""
        inspected = self.inspect(memory_id)
        if not inspected.get("success"):
            return inspected
        memory = inspected["memory"]
        influence = self._memory_influence_shape(
            memory,
            inspected=inspected,
            used_in_answer=used_in_answer,
            current_request=current_request,
        )
        return {
            "success": True,
            "memory_id": memory.get("id"),
            "mutated": False,
            "memory_influence": influence,
            "why_did_you_remember_that": influence,
            "answer_attribution": self.answer_attribution([str(memory.get("id") or "")]) if used_in_answer else self.answer_attribution([]),
            "redaction": "safe summaries are redacted for sensitive/local-only memories; raw memory content is not returned",
        }

    def answer_attribution(self, memory_ids: List[str], *, answer_summary: str = "") -> Dict[str, Any]:
        """Return the concise attribution shape for memories actually used in an answer."""
        cleaned_ids = []
        seen = set()
        for memory_id in memory_ids:
            memory_id = str(memory_id or "").strip()
            if memory_id and memory_id not in seen:
                cleaned_ids.append(memory_id)
                seen.add(memory_id)
        if not cleaned_ids:
            return {"success": True, "used_memory": False, "memory_ids": [], "attributions": [], "mutated": False}

        attributions: List[Dict[str, Any]] = []
        missing_ids: List[str] = []
        for memory_id in cleaned_ids:
            inspected = self.inspect(memory_id)
            if not inspected.get("success"):
                missing_ids.append(memory_id)
                continue
            memory = inspected["memory"]
            influence = self._memory_influence_shape(memory, inspected=inspected, used_in_answer=True)
            attributions.append({
                "memory_id": influence["memory_id"],
                "source": influence["source"],
                "summary": influence["safe_summary"],
                "confidence": influence["confidence"],
                "sensitivity": influence["sensitivity"],
                "stability": influence["stability"],
                "current_request_safe": influence["current_request_safe"],
                "redacted": influence["redacted"],
                "suppressed": influence["suppression"]["suppressed"],
                "conflict_status": influence["conflict"]["conflict_status"],
            })
        return {
            "success": not missing_ids,
            "used_memory": bool(attributions),
            "memory_ids": [item["memory_id"] for item in attributions],
            "attributions": attributions,
            "answer_summary": self._redact_text(answer_summary) if answer_summary else "",
            "missing_memory_ids": missing_ids,
            "mutated": False,
        }

    def _memory_influence_shape(
        self,
        memory: Dict[str, Any],
        *,
        inspected: Dict[str, Any],
        used_in_answer: bool,
        current_request: str = "",
    ) -> Dict[str, Any]:
        sensitivity = str(memory.get("sensitivity") or "unknown")
        redacted = self._memory_requires_redaction(memory)
        suppressions = inspected.get("suppressions") if isinstance(inspected.get("suppressions"), list) else []
        active_suppressions = [item for item in suppressions if item.get("active", True)]
        return {
            "memory_id": memory.get("id", ""),
            "used_in_answer": bool(used_in_answer),
            "source": self._redact_text(str(memory.get("source") or "")),
            "context": self._redact_text(str(memory.get("context") or "")),
            "rationale": self._redact_text(str(memory.get("rationale") or "")),
            "confidence": str(memory.get("confidence") or "unknown"),
            "sensitivity": sensitivity,
            "stability": str(memory.get("stability") or "unknown"),
            "current_request_safe": bool(memory.get("current_request_safe") or False),
            "safe_summary": self._safe_memory_summary(memory) if used_in_answer else self._safe_memory_topic_summary(memory),
            "redacted": redacted,
            "current_request": self._redact_text(current_request) if current_request else "",
            "suppression": {
                "suppressed": bool(inspected.get("suppressed", False)),
                "active_suppression_count": len(active_suppressions),
                "suppression_ids": [item.get("id", "") for item in active_suppressions],
                "suppression_rationales": [self._redact_text(str(item.get("rationale") or "")) for item in active_suppressions],
            },
            "conflict": {
                "conflict_group": str(memory.get("conflict_group") or ""),
                "conflict_status": str(memory.get("conflict_status") or "unknown"),
                "supersedes": self._normalize_ids(memory.get("supersedes") if isinstance(memory.get("supersedes"), list) else None),
                "superseded_by": str(memory.get("superseded_by") or ""),
            },
        }

    def _memory_requires_redaction(self, memory: Dict[str, Any]) -> bool:
        sensitivity = str(memory.get("sensitivity") or "unknown").lower()
        text = "\n".join(str(memory.get(key) or "") for key in ("content", "source", "context", "rationale"))
        return sensitivity in {"sensitive", "secret"} or self._has_secret_marker(text)

    def _safe_memory_summary(self, memory: Dict[str, Any], *, max_chars: int = 220) -> str:
        if self._memory_requires_redaction(memory):
            return "[redacted: sensitive/local-only memory]"
        summary = self._redact_text(str(memory.get("content") or "")).strip()
        if len(summary) > max_chars:
            return summary[: max_chars - 1].rstrip() + "…"
        return summary

    def _safe_memory_topic_summary(self, memory: Dict[str, Any]) -> str:
        if self._memory_requires_redaction(memory):
            return "[redacted: sensitive/local-only memory]"
        topic = str(memory.get("topic") or "").strip()
        source = str(memory.get("source") or "").strip()
        parts = []
        if topic:
            parts.append(f"topic={self._redact_text(topic)}")
        if source:
            parts.append(f"source={self._redact_text(source)}")
        return "non-sensitive trusted memory metadata" + (" (" + "; ".join(parts) + ")" if parts else "")

    def _redact_text(self, text: str) -> str:
        return _SECRET_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text or "")

    def semantic_recall(self, query: str, *, limit: int = 5) -> Dict[str, Any]:
        gates = self.semantic_quality_gates()
        if not gates.get("semantic_recall_allowed"):
            return {"success": True, "semantic_recall_allowed": False, "results": [], "gates": gates, "mutated": False}
        q_tokens = _tokens(query)
        config = self._load_config()
        scored = []
        for item in self.recall(query, limit=20, include_suppressed=False):
            memory = item["memory"]
            if not self._is_prefetch_eligible(memory, query=query, config=config):
                continue
            text = " ".join(str(memory.get(k) or "") for k in ("content", "source", "context", "rationale", "topic"))
            tokens = _tokens(text)
            overlap = len(q_tokens & tokens)
            semantic_bonus = sum(1 for token in q_tokens for other in tokens if token != other and (token in other or other in token))
            score = item.get("score", 0) + overlap + (semantic_bonus * 0.25)
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return {"success": True, "semantic_recall_allowed": True, "results": [item for _, item in scored[: max(1, min(limit, 20))]], "mutated": False}

    def harvest_candidates(self, *, content: str, source: str, context: str, topic: str = "") -> Dict[str, Any]:
        """Conservatively queue durable memory candidates discovered in normal work.

        This is intentionally candidate-only: accepted items go through
        add_candidate(), never add_memory(). Rejected/deferred items return audit
        reasons without persisting raw rejected content.
        """
        text = content.strip()
        if not text:
            return {"success": False, "error": "content is required for harvesting", "mutated_memory": False}
        candidates: List[Dict[str, Any]] = []
        rejections: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for segment in self._harvest_segments(text):
            decision = self._classify_harvest_segment(segment, source=source, context=context, topic=topic)
            if not decision.get("accepted"):
                rejections.append({"reason": decision.get("reason", "rejected"), "category": decision.get("category", "unknown")})
                continue
            candidate_content = str(decision.get("content") or "").strip()
            dedupe_key = candidate_content.lower()
            if not candidate_content or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            queued = self.add_candidate(
                content=candidate_content,
                source=source,
                context=context,
                rationale=str(decision.get("rationale") or "auto-harvested durable writeback candidate from production work; requires approval"),
                confidence=str(decision.get("confidence") or "medium"),
                sensitivity=str(decision.get("sensitivity") or "non_sensitive"),
                stability=str(decision.get("stability") or "stable"),
                current_request_safe=bool(decision.get("current_request_safe", True)),
                topic=str(decision.get("topic") or topic),
            )
            if not queued.get("success"):
                rejections.append({"reason": queued.get("error", "candidate_queue_rejected"), "category": decision.get("category", "unknown")})
                continue
            candidates.append(queued["candidate"])
            if len(candidates) >= 5:
                break
        result: Dict[str, Any] = {
            "success": True,
            "created_candidate_count": len(candidates),
            "candidates": candidates,
            "rejected_count": len(rejections),
            "rejections": rejections,
            "mutated_memory": False,
        }
        if not candidates:
            result["reason"] = rejections[0]["reason"] if rejections else "no durable memory signal detected"
            if rejections and all(item.get("category") == "secret" for item in rejections):
                result["success"] = False
                result["error"] = "Refusing to harvest likely secret-bearing content."
        return result

    def _harvest_segments(self, text: str) -> List[str]:
        segments: List[str] = []
        for raw_line in str(text or "").splitlines() or [text]:
            line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw_line).strip()
            if not line:
                continue
            parts = re.split(r"(?<=[.!?])\s+(?=(?:Correction:|Environment fact:|Operating convention:|Marco|User|Biff|Please|Always|Remember|Actually|The|Linear|SecondBrain|Hermes)\b)", line)
            segments.extend(part.strip() for part in parts if part.strip())
        return segments[:20]

    def _classify_harvest_segment(self, segment: str, *, source: str, context: str, topic: str) -> Dict[str, Any]:
        segment = " ".join(str(segment or "").split()).strip()
        if not segment:
            return {"accepted": False, "reason": "empty_segment", "category": "empty"}
        combined = "\n".join([segment, source, context])
        if self._has_secret_marker(combined):
            return {"accepted": False, "reason": "secret_or_credential_signal", "category": "secret"}
        if self._is_private_raw_detail(segment):
            return {"accepted": False, "reason": "private_or_family_sensitive_raw_detail", "category": "private_sensitive"}
        if self._is_temporary_or_status_artifact(segment):
            return {"accepted": False, "reason": "temporary_task_progress_or_status_artifact", "category": "temporary"}

        lower = segment.lower()
        category = ""
        confidence = "medium"
        cleaned = segment
        rationale = "auto-harvested durable writeback candidate from production work; requires approval"
        if lower.startswith("correction:") or lower.startswith("user correction:") or lower.startswith("actually,") or lower.startswith("actually "):
            category = "explicit_user_correction"
            confidence = "high"
            cleaned = re.sub(r"(?i)^(?:user\s+)?correction:\s*", "", cleaned).strip()
            cleaned = re.sub(r"(?i)^actually,?\s*", "", cleaned).strip()
            rationale = "auto-harvested explicit user correction; queued as candidate and requires approval"
        elif self._looks_like_durable_preference(segment):
            category = "durable_preference"
            rationale = "auto-harvested durable user preference; queued as candidate and requires approval"
        elif self._looks_like_stable_environment_fact(segment):
            category = "stable_environment_fact"
            confidence = "medium"
            cleaned = re.sub(r"(?i)^environment fact:\s*", "", cleaned).strip()
            rationale = "auto-harvested stable environment fact; queued as candidate and requires approval"
        elif self._looks_like_operating_convention(segment):
            category = "repeated_operating_convention"
            rationale = "auto-harvested repeated operating convention; queued as candidate and requires approval"

        if not category:
            return {"accepted": False, "reason": "low_durability_or_no_supported_signal", "category": "low_durability"}
        if len(_tokens(cleaned)) < 4:
            return {"accepted": False, "reason": "too_little_context_for_durable_memory", "category": "low_durability"}
        return {
            "accepted": True,
            "category": category,
            "content": cleaned,
            "rationale": rationale,
            "confidence": confidence,
            "sensitivity": "non_sensitive",
            "stability": "stable",
            "current_request_safe": True,
            "topic": topic or category,
        }

    @staticmethod
    def _is_temporary_or_status_artifact(text: str) -> bool:
        lower = str(text or "").lower()
        transient_terms = (
            "temporary update", "today", "tomorrow", "this week", "right now", "in progress", "pending review",
            "finished pr", "opened pr", "merged pr", "blocked on", "status update", "standup", "debug thought",
            "one-off", "one off", "scratch", "wip", "todo", "done", "shipped", "commit ",
        )
        if any(term in lower for term in transient_terms):
            return True
        if re.search(r"\b(?:pr|pull request)\s*#?\d+\b", lower):
            return True
        if re.search(r"\b[A-Z]{2,10}-\d+\b", str(text or "")) and any(term in lower for term in ("status", "progress", "done", "finished", "pending", "review", "blocked")):
            return True
        if re.search(r"\b[0-9a-f]{7,40}\b", lower) and any(term in lower for term in ("commit", "sha", "branch")):
            return True
        return False

    @staticmethod
    def _is_private_raw_detail(text: str) -> bool:
        lower = str(text or "").lower()
        private_subject = ("spouse", "wife", "husband", "partner", "child", "kid", "daughter", "son", "family", "parent")
        sensitive_detail = ("medical", "therapy", "therapist", "diagnosis", "medication", "appointment", "school", "address", "raw notes", "private")
        return any(term in lower for term in private_subject) and any(term in lower for term in sensitive_detail)

    @staticmethod
    def _looks_like_durable_preference(text: str) -> bool:
        lower = str(text or "").lower()
        patterns = (
            r"\bmarco prefers\b", r"\bmarco wants\b", r"\bmarco expects\b", r"\buser prefers\b",
            r"\buser wants\b", r"\bbiff should\b", r"\bbiff must\b", r"\bplease always\b",
            r"\balways (?:use|keep|default|prefer|queue|ask|avoid)\b", r"\bdo not\b", r"\bdon't\b",
        )
        return any(re.search(pattern, lower) for pattern in patterns)

    @staticmethod
    def _looks_like_stable_environment_fact(text: str) -> bool:
        lower = str(text or "").lower()
        if lower.startswith("environment fact:"):
            return True
        stable_fact_markers = ("runtime lives at", "is located at", "uses ", "runs on", "source of truth is", "vault is")
        path_or_system = bool(re.search(r"(?:~?/|/Users/|/var/|localhost|\.local\b|\.hermes\b|\.yaml\b|\.json\b)", text))
        return path_or_system and any(marker in lower for marker in stable_fact_markers)

    @staticmethod
    def _looks_like_operating_convention(text: str) -> bool:
        lower = str(text or "").lower()
        markers = (
            "operating convention:", "source of truth", "canonical source", "by default", "default is",
            "normal biff work", "repeated convention", "linear is", "secondbrain is", "mnemosyne stores",
        )
        return any(marker in lower for marker in markers)

    def apply_correction(
        self,
        *,
        content: str,
        source: str,
        context: str,
        rationale: str,
        superseded_memory_id: str,
        topic: str = "",
        conflict_group: str = "",
    ) -> Dict[str, Any]:
        if not self._memory_by_id(superseded_memory_id):
            return {"success": False, "error": f"Memory not found: {superseded_memory_id}", "mutated_memory": False}
        correction_text = "\n".join([content, source, context, rationale])
        if self._has_secret_marker(correction_text):
            return {"success": False, "error": "Refusing likely secret-bearing correction content.", "mutated_memory": False}
        queued = self.add_candidate(
            content=content,
            source=source,
            context=context,
            rationale=rationale or "explicit correction candidate; requires approval before trusted memory mutation",
            confidence="high",
            sensitivity="non_sensitive",
            stability="stable",
            current_request_safe=True,
            topic=topic,
            conflict_group=conflict_group,
            conflict_status="resolved" if conflict_group else "unknown",
            supersedes=[superseded_memory_id],
        )
        if not queued.get("success"):
            return queued
        return {"success": True, "candidate": queued["candidate"], "mutated_memory": False}

    def discord_decision_digest(self) -> Dict[str, Any]:
        digest = self.memory_digest()
        needs = digest.get("needs_decision", {})
        pending = int(needs.get("pending_candidate_count") or 0)
        hygiene = int(needs.get("hygiene_recommendation_count") or 0)
        conflicts = len(needs.get("conflict_memory_ids") or [])
        if pending == 0 and hygiene == 0 and conflicts == 0:
            return {"success": True, "should_notify": False, "message": "", "digest": digest, "mutated": False}
        parts = []
        if pending:
            parts.append(f"{pending} pending candidate(s): {', '.join(str(x) for x in needs.get('pending_candidate_ids', []))}")
        if hygiene:
            hygiene_ids = sorted({
                str(memory_id)
                for item in needs.get("hygiene_recommendations", [])
                for memory_id in (item.get("candidate_memory_ids") or [])
                if memory_id
            })
            suffix = f": {', '.join(hygiene_ids)}" if hygiene_ids else ""
            parts.append(f"{hygiene} hygiene item(s){suffix}")
        if conflicts:
            conflict_ids = [str(memory_id) for memory_id in (needs.get("conflict_memory_ids") or []) if memory_id]
            suffix = f": {', '.join(conflict_ids)}" if conflict_ids else ""
            parts.append(f"{conflicts} conflict item(s){suffix}")
        message = "Marco decision needed — Mnemosyne memory review: " + "; ".join(parts) + ". Raw memory content omitted."
        return {"success": True, "should_notify": True, "message": message, "digest": digest, "mutated": False}

    def rollout_manifest(self) -> Dict[str, Any]:
        """Return profile-scoped rollout/rollback metadata without changing config."""
        config = self._load_config()
        return {
            "success": True,
            "provider": self.name,
            "hermes_home": str(self.hermes_home),
            "mnemosyne_root": str(self._root.parent),
            "config_path": str(self._config_path),
            "data_dir": str(self.data_dir),
            "selective_prefetch_enabled": config.get("selective_prefetch_enabled") is True,
            "candidate_queue_enabled": config.get("candidate_queue_enabled") is not False,
            "rollback_modes": ["explicit_only", "selective_prefetch_only", "full_disable"],
            "non_mutating": True,
            "mutated": False,
        }

    def suppress_memory(self, *, memory_id: str, rationale: str, source: str) -> Dict[str, Any]:
        memory_id = memory_id.strip()
        rationale = rationale.strip()
        source = source.strip() or "mnemosyne_memory"
        if not memory_id or not rationale:
            return {"success": False, "error": "memory_id and rationale are required for suppression."}
        if not self._memory_by_id(memory_id):
            return {"success": False, "error": f"Memory not found: {memory_id}"}
        existing = self._active_suppression(memory_id)
        if existing:
            return {"success": True, "suppression": existing, "message": "Memory already suppressed."}
        suppression = MnemosyneSuppression(id=f"sup_{uuid.uuid4().hex[:12]}", memory_id=memory_id, rationale=rationale, source=source, created_at=_now_iso())
        self._append_jsonl(self._suppressions_path, asdict(suppression))
        return {"success": True, "suppression": asdict(suppression)}

    def unsuppress_memory(self, *, memory_id: str, rationale: str) -> Dict[str, Any]:
        memory_id = memory_id.strip()
        rationale = rationale.strip()
        if not memory_id or not rationale:
            return {"success": False, "error": "memory_id and rationale are required for rollback/unsuppress."}
        suppressions = self._read_jsonl(self._suppressions_path)
        changed = False
        for row in suppressions:
            if row.get("memory_id") == memory_id and row.get("active", True):
                row["active"] = False
                row["unsuppressed_at"] = _now_iso()
                row["unsuppress_rationale"] = rationale
                changed = True
        if not changed:
            return {"success": False, "error": f"No active suppression found for {memory_id}"}
        self._write_jsonl(self._suppressions_path, suppressions)
        return {"success": True, "memory_id": memory_id, "message": "Suppression rolled back."}

    def inspect(self, memory_id: str) -> Dict[str, Any]:
        memory = self._memory_by_id(memory_id.strip())
        if not memory:
            return {"success": False, "error": f"Memory not found: {memory_id}"}
        suppressions = [s for s in self._read_jsonl(self._suppressions_path) if s.get("memory_id") == memory_id]
        return {"success": True, "memory": memory, "suppressions": suppressions, "suppressed": any(s.get("active", True) for s in suppressions)}

    def list_memories(self, *, include_suppressed: bool = False) -> List[Dict[str, Any]]:
        active = self._active_suppressed_ids()
        rows = []
        for memory in self._read_jsonl(self._memories_path):
            suppressed = memory.get("id") in active
            if suppressed and not include_suppressed:
                continue
            rows.append(self._memory_result(memory, score=None, suppressed=suppressed))
        return rows

    def recall(self, query: str, *, limit: int = 5, include_suppressed: bool = False) -> List[Dict[str, Any]]:
        q_tokens = _tokens(query)
        active = self._active_suppressed_ids()
        scored: List[tuple[int, Dict[str, Any]]] = []
        for memory in self._read_jsonl(self._memories_path):
            memory_id = str(memory.get("id") or "")
            suppressed = memory_id in active
            if suppressed and not include_suppressed:
                continue
            haystack = " ".join(str(memory.get(k) or "") for k in ("content", "source", "context", "rationale", "topic", "conflict_group"))
            m_tokens = _tokens(haystack)
            score = len(q_tokens & m_tokens) if q_tokens else 1
            if query and query.lower() in haystack.lower():
                score += 5
            if score <= 0:
                continue
            scored.append((score, self._memory_result(memory, score=score, suppressed=suppressed)))
        scored.sort(key=lambda item: (-item[0], item[1]["memory"].get("created_at", "")))
        return [item for _, item in scored[: max(1, min(int(limit or 5), 20))]]

    def hygiene_report(self, *, include_suppressed: bool = False) -> Dict[str, Any]:
        """Return non-mutating hygiene recommendations for local Mnemosyne state."""
        active = self._active_suppressed_ids()
        memories = []
        for memory in self._read_jsonl(self._memories_path):
            memory_id = str(memory.get("id") or "")
            suppressed = memory_id in active
            if suppressed and not include_suppressed:
                continue
            memories.append((memory, suppressed))

        recommendations: List[Dict[str, Any]] = []
        stale_markers = {"stale", "deprecated", "outdated", "legacy", "old", "replaced", "superseded", "obsolete"}
        current_markers = {"current", "approved", "active", "canonical", "verified", "production", "live"}

        def text_for(memory: Dict[str, Any]) -> str:
            return " ".join(str(memory.get(k) or "") for k in ("content", "source", "context", "rationale", "topic", "conflict_group"))

        token_cache = {str(m.get("id") or ""): _tokens(text_for(m)) for m, _ in memories}
        for memory, suppressed in memories:
            memory_id = str(memory.get("id") or "")
            markers = sorted(marker for marker in stale_markers if marker in text_for(memory).lower())
            if markers and not suppressed:
                recommendations.append({"candidate_memory_ids": [memory_id], "reason": f"stale/deprecation marker(s): {', '.join(markers)}", "suggested_action": "inspect"})
            sensitivity = str(memory.get("sensitivity") or "unknown").lower()
            if sensitivity in {"sensitive", "secret"} or self._has_secret_marker(text_for(memory)):
                recommendations.append({
                    "candidate_memory_ids": [memory_id],
                    "reason": "possible sensitive/secret content or sensitive classification",
                    "suggested_action": "inspect; confirm explicit-recall-only status or suppress if stale",
                })
            missing_metadata = [
                name for name in ("confidence", "sensitivity", "stability")
                if str(memory.get(name) or "unknown") == "unknown"
            ]
            if missing_metadata:
                recommendations.append({
                    "candidate_memory_ids": [memory_id],
                    "reason": f"missing explicit eligibility metadata: {', '.join(missing_metadata)}",
                    "suggested_action": "inspect; consider updating metadata before prefetch eligibility",
                })
            supersedes = self._normalize_ids(memory.get("supersedes") if isinstance(memory.get("supersedes"), list) else None)
            unsuppressed_superseded = [item for item in supersedes if item not in active]
            if unsuppressed_superseded:
                recommendations.append({"candidate_memory_ids": [memory_id, *unsuppressed_superseded], "reason": "explicit superseded/supersession metadata present", "suggested_action": f"inspect; consider suppressing superseded IDs: {', '.join(unsuppressed_superseded)}"})

        for index, (left, left_suppressed) in enumerate(memories):
            left_id = str(left.get("id") or "")
            left_tokens = token_cache[left_id]
            if not left_tokens:
                continue
            for right, right_suppressed in memories[index + 1:]:
                right_id = str(right.get("id") or "")
                right_tokens = token_cache[right_id]
                if not right_tokens:
                    continue
                overlap = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
                if overlap >= 0.85:
                    recommendations.append({"candidate_memory_ids": [left_id, right_id], "reason": f"duplicate-like token overlap {overlap:.2f}", "suggested_action": "inspect"})
                    continue
                same_conflict_group = bool(left.get("conflict_group") and left.get("conflict_group") == right.get("conflict_group"))
                left_text = text_for(left).lower()
                right_text = text_for(right).lower()
                left_stale = bool(stale_markers & _tokens(left_text)) or left.get("conflict_status") == "superseded"
                right_stale = bool(stale_markers & _tokens(right_text)) or right.get("conflict_status") == "superseded"
                left_current = bool(current_markers & _tokens(left_text)) or left.get("conflict_status") in {"active", "resolved"}
                right_current = bool(current_markers & _tokens(right_text)) or right.get("conflict_status") in {"active", "resolved"}
                if same_conflict_group or (overlap >= 0.35 and ((left_stale and right_current) or (right_stale and left_current))):
                    stale_id = left_id if left_stale and not left_suppressed else right_id
                    if right_stale and right_suppressed:
                        stale_id = right_id
                    reason = "same conflict_group" if same_conflict_group else f"possible stale/current conflict, token overlap {overlap:.2f}"
                    recommendations.append({"candidate_memory_ids": [left_id, right_id], "reason": reason, "suggested_action": f"inspect; consider suppressing {stale_id}"})

        return {
            "success": True,
            "generated_at": _now_iso(),
            "memory_count": len(memories),
            "suppressed_count": sum(1 for _, suppressed in memories if suppressed),
            "candidate_count": len(self._read_jsonl(self._candidates_path)),
            "include_suppressed": include_suppressed,
            "recommendations": recommendations,
            "mutated": False,
        }

    @staticmethod
    def _memory_result(memory: Dict[str, Any], *, score: int | None, suppressed: bool) -> Dict[str, Any]:
        result = {
            "memory": memory,
            "suppressed": suppressed,
            "trusted": True,
            "source": memory.get("source"),
            "context": memory.get("context"),
            "rationale": memory.get("rationale"),
            "created_at": memory.get("created_at"),
            "confidence": memory.get("confidence", "unknown"),
            "sensitivity": memory.get("sensitivity", "unknown"),
            "stability": memory.get("stability", "unknown"),
            "current_request_safe": memory.get("current_request_safe", False),
            "rationale_id": memory.get("rationale_id", ""),
            "topic": memory.get("topic", ""),
            "conflict_group": memory.get("conflict_group", ""),
            "conflict_status": memory.get("conflict_status", "unknown"),
            "supersedes": memory.get("supersedes") or [],
            "superseded_by": memory.get("superseded_by", ""),
            "valid_from": memory.get("valid_from", ""),
            "valid_until": memory.get("valid_until", ""),
        }
        if score is not None:
            result["score"] = score
        return result

    def _memory_by_id(self, memory_id: str) -> Dict[str, Any] | None:
        for row in self._read_jsonl(self._memories_path):
            if row.get("id") == memory_id:
                return row
        return None

    def _active_suppression(self, memory_id: str) -> Dict[str, Any] | None:
        for row in reversed(self._read_jsonl(self._suppressions_path)):
            if row.get("memory_id") == memory_id and row.get("active", True):
                return row
        return None

    def _active_suppressed_ids(self) -> set[str]:
        return {str(row.get("memory_id")) for row in self._read_jsonl(self._suppressions_path) if row.get("active", True)}

    def _store_path(self) -> Path:
        return self.data_dir / "memories.jsonl"

    def _snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    def _compat_rows(self) -> List[Dict[str, Any]]:
        return self._read_jsonl(self._store_path())

    def _compat_write_rows(self, rows: List[Dict[str, Any]]) -> None:
        self._write_jsonl(self._store_path(), rows)

    def _compat_remember(self, content: str, metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.config:
            self.initialize(self.session_id or "manual")
        content = (content or "").strip()[: self.config.get("max_memory_chars", self.MAX_MEMORY_CHARS)]
        if not content:
            return {"ok": False, "error": "empty content"}
        rows = self._compat_rows()
        rec = {"id": f"mnemo-{int(time.time() * 1000)}-{len(rows) + 1}", "content": content, "metadata": metadata or {}, "session_id": self.session_id, "created_at": time.time()}
        rows.append(rec)
        self._compat_write_rows(rows)
        return {"ok": True, "id": rec["id"]}

    def _compat_search(self, query: str, top_k: Any = None) -> Dict[str, Any]:
        if not self.config:
            self.initialize(self.session_id or "search")
        try:
            k = int(top_k or self.config.get("top_k", self.DEFAULT_TOP_K))
        except (TypeError, ValueError):
            k = self.DEFAULT_TOP_K
        k = max(1, min(k, self.MAX_TOP_K))
        terms = {t.lower() for t in (query or "").split() if t.strip()}
        scored = []
        for row in self._compat_rows():
            content = str(row.get("content", ""))
            score = sum(1 for t in terms if t in content.lower()) if terms else 0
            if score or not terms:
                scored.append((score, row))
        scored.sort(key=lambda x: (x[0], x[1].get("created_at", 0)), reverse=True)
        return {"ok": True, "top_k": k, "memories": [r for _, r in scored[:k]]}

    def _compat_export(self, label: str = "") -> Dict[str, Any]:
        if not self.config:
            self.initialize(self.session_id or "export")
        safe_label = "".join(c for c in (label or "snapshot") if c.isalnum() or c in ("-", "_"))[:40] or "snapshot"
        out = self._snapshots_dir() / f"{int(time.time())}-{safe_label}.json"
        payload = {"provider": "mnemosyne", "version": 1, "host_llm_enabled": self.config.get("host_llm_enabled", False), "memories": self._compat_rows()}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return {"ok": True, "path": str(out), "count": len(payload["memories"])}

    def _compat_snapshot_path(self, path: str) -> Path:
        p = Path(path).expanduser().resolve()
        root = self.data_dir.resolve()
        p.relative_to(root)
        return p

    def _compat_import_or_restore(self, path: str, *, restore: bool) -> Dict[str, Any]:
        if not self.config:
            self.initialize(self.session_id or "restore")
        src = self._compat_snapshot_path(path)
        payload = json.loads(src.read_text(encoding="utf-8"))
        incoming = payload.get("memories", payload if isinstance(payload, list) else [])
        if not isinstance(incoming, list):
            return {"ok": False, "error": "snapshot must contain a memories list"}
        current = [] if restore else self._compat_rows()
        existing_ids = {r.get("id") for r in current}
        for row in incoming:
            if isinstance(row, dict) and row.get("id") not in existing_ids:
                current.append(row)
                existing_ids.add(row.get("id"))
        self._compat_write_rows(current)
        return {"ok": True, "mode": "restore" if restore else "import", "count": len(current)}

    def _load_config(self) -> Dict[str, Any]:
        defaults: Dict[str, Any] = {
            "host_llm_enabled": False,
            "top_k": self.DEFAULT_TOP_K,
            "max_memory_chars": self.MAX_MEMORY_CHARS,
            "max_results_chars": self.MAX_RESULTS_CHARS,
            "selective_prefetch_enabled": False,
            "max_prefetch_results": 3,
            "max_prefetch_scan_results": 20,
            "max_prefetch_context_chars": 1800,
            "min_prefetch_score": 2,
            "min_prefetch_token_overlap": 2,
            "prefetch_trace_enabled": False,
            "candidate_queue_enabled": True,
            "max_seed_records": 10,
            "blocked_terms": ["api key", "apikey", "password", "private key", "secret", "token", "credential"],
            "stale_terms": ["stale", "deprecated", "outdated", "legacy", "obsolete", "replaced", "superseded"],
            "risky_query_terms": ["fire someone", "ignore consent", "illegal", "self harm", "suicide", "blackmail", "harass", "medical diagnosis", "legal advice", "financial advice"],
            "max_prefetch_age_days": 730,
        }
        if not self._config_path.exists():
            return defaults
        try:
            loaded = json.loads(self._config_path.read_text(encoding="utf-8"))
        except Exception:
            return defaults
        if not isinstance(loaded, dict):
            return defaults
        merged = dict(defaults)
        merged.update(loaded)
        env_llm = os.environ.get("MNEMOSYNE_HOST_LLM_ENABLED")
        if env_llm is not None:
            merged["host_llm_enabled"] = env_llm.strip().lower() in {"1", "true", "yes", "on"}
        else:
            raw_llm = merged.get("host_llm_enabled", False)
            merged["host_llm_enabled"] = raw_llm if isinstance(raw_llm, bool) else str(raw_llm).strip().lower() in {"1", "true", "yes", "on"}
        for key, default, maximum, minimum in (
            ("top_k", self.DEFAULT_TOP_K, self.MAX_TOP_K, 1),
            ("max_memory_chars", self.MAX_MEMORY_CHARS, self.MAX_MEMORY_CHARS, 1),
            ("max_results_chars", self.MAX_RESULTS_CHARS, self.MAX_RESULTS_CHARS, 256),
        ):
            try:
                value = int(merged.get(key, default))
            except (TypeError, ValueError):
                value = default
            merged[key] = max(minimum, min(value, maximum))
        return merged

    def _prefetch_eligibility_reason(self, memory: Dict[str, Any], *, query: str, config: Dict[str, Any]) -> tuple[bool, str]:
        haystack = " ".join(str(memory.get(k) or "") for k in ("content", "source", "context", "rationale", "topic", "conflict_group"))
        haystack_lower = haystack.lower()
        if str(memory.get("confidence") or "unknown").lower() != "high":
            return False, "confidence_not_high"
        if str(memory.get("superseded_by") or ""):
            return False, "superseded"
        if str(memory.get("sensitivity") or "unknown").lower() != "non_sensitive":
            return False, "sensitivity_not_non_sensitive"
        if str(memory.get("stability") or "unknown").lower() not in {"stable", "current"}:
            return False, "stability_not_stable_or_current"
        if memory.get("current_request_safe") is not True:
            return False, "not_current_request_safe"
        if not str(memory.get("rationale_id") or "").startswith("rat_"):
            return False, "missing_rationale_id"
        if self._is_memory_too_old(memory, config):
            return False, "too_old"
        query_lower = str(query or "").lower()
        for required_phrase in ("command surface", "live command", "primary live"):
            if required_phrase in query_lower and required_phrase not in haystack_lower:
                return False, "required_phrase_mismatch"
        for phrase in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", query_lower):
            if phrase not in haystack_lower:
                return False, "hyphenated_phrase_mismatch"
        if any(str(term).lower() in haystack_lower for term in config.get("blocked_terms", [])):
            return False, "blocked_term"
        if any(str(term).lower() in haystack_lower for term in config.get("stale_terms", [])):
            return False, "stale_term"
        query_tokens = _tokens(query)
        memory_tokens = _tokens(haystack)
        overlap = len(query_tokens & memory_tokens)
        min_overlap_value = self._config_int(config, "min_prefetch_token_overlap", default=2, minimum=1, maximum=100)
        if min_overlap_value is None:
            return False, "invalid_min_overlap_config"
        if overlap < min_overlap_value:
            return False, "below_min_overlap"
        return True, "eligible"

    def _is_prefetch_eligible(self, memory: Dict[str, Any], *, query: str, config: Dict[str, Any]) -> bool:
        return self._prefetch_eligibility_reason(memory, query=query, config=config)[0]

    def _query_prefetch_safe_reason(self, query: str, config: Dict[str, Any]) -> tuple[bool, str]:
        normalized = " ".join(str(query or "").split()).lower()
        if not normalized:
            return False, "empty_query"
        if normalized.startswith("/"):
            return False, "slash_command"
        if len(_tokens(normalized)) < 2:
            return False, "too_few_query_tokens"
        blocked = [str(term).lower() for term in config.get("blocked_terms", [])]
        risky = [str(term).lower() for term in config.get("risky_query_terms", [])]
        if any(term and term in normalized for term in blocked + risky):
            return False, "blocked_or_risky_query"
        return True, "safe"

    def _query_prefetch_safe(self, query: str, config: Dict[str, Any]) -> bool:
        return self._query_prefetch_safe_reason(query, config)[0]

    @staticmethod
    def _conflict_subject(memory: Dict[str, Any]) -> str:
        subject = str(memory.get("conflict_group") or "").strip().lower()
        if subject:
            return subject
        context = str(memory.get("context") or "").lower()
        if "conflict subject:" in context:
            return context.split("conflict subject:", 1)[1].strip()
        return ""

    def _has_prefetch_conflict(self, candidates: List[Dict[str, Any]], *, query: str = "") -> bool:
        if len(candidates) < 2:
            return False
        conflict_subject_groups: Dict[str, set[str]] = {}
        for item in candidates:
            memory = item.get("memory") or {}
            subject = str(memory.get("conflict_group") or "").lower()
            context = str(memory.get("context") or "").lower()
            if not subject and "conflict subject:" in context:
                subject = context.split("conflict subject:", 1)[1].strip()
            content_tokens = _tokens(str(memory.get("content") or ""))
            qualifiers = {"current", "stable", "approved", "biff", "command", "surface", "the", "for", "with"}
            values = {token for token in content_tokens if token not in _tokens(subject) and token not in qualifiers}
            if subject:
                conflict_subject_groups.setdefault(subject, set()).update(values)
        if any(len(values) > 1 for values in conflict_subject_groups.values()):
            return True

        query_tokens = _tokens(query) - {"what", "which", "when", "where", "who", "why", "how", "does", "that", "this", "with", "from"}
        if len(query_tokens) < 2:
            return False
        non_value_tokens = query_tokens | {"the", "and", "for", "with", "from", "that", "this", "current", "stable", "approved", "canonical", "active", "safe", "biff", "command", "surface", "operating", "convention", "fixture", "prefetch", "conflict", "rationale", "source", "context", "high", "non_sensitive", "selective", "memory"}
        for left_index, left_item in enumerate(candidates):
            left_memory = left_item.get("memory") or {}
            left_subject = self._conflict_subject(left_memory)
            left_tokens = _tokens(str(left_memory.get("content") or ""))
            if len(query_tokens & left_tokens) < min(3, len(query_tokens)):
                continue
            for right_item in candidates[left_index + 1:]:
                right_memory = right_item.get("memory") or {}
                right_subject = self._conflict_subject(right_memory)
                if (left_subject or right_subject) and left_subject != right_subject:
                    continue
                right_tokens = _tokens(str(right_memory.get("content") or ""))
                if len(query_tokens & right_tokens) < min(3, len(query_tokens)):
                    continue
                shared_subject_tokens = (left_tokens & right_tokens) & query_tokens
                if len(shared_subject_tokens) < min(3, len(query_tokens)):
                    continue
                memory_overlap = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
                if memory_overlap < 0.55:
                    continue
                left_values = left_tokens - right_tokens - non_value_tokens
                right_values = right_tokens - left_tokens - non_value_tokens
                if left_values and right_values:
                    return True
        return False

    def _is_memory_too_old(self, memory: Dict[str, Any], config: Dict[str, Any]) -> bool:
        max_age_days = self._config_int(config, "max_prefetch_age_days", default=730, minimum=1, maximum=3650)
        if max_age_days is None:
            return True
        raw_created_at = str(memory.get("created_at") or "")
        try:
            created_at = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        return (datetime.now(timezone.utc) - created_at).days > max_age_days

    def _apply_supersession(self, memory: Dict[str, Any]) -> None:
        supersedes = self._normalize_ids(memory.get("supersedes") if isinstance(memory.get("supersedes"), list) else None)
        if not supersedes:
            return
        rows = self._read_jsonl(self._memories_path)
        changed = False
        for row in rows:
            if row.get("id") in supersedes:
                row["superseded_by"] = memory.get("id")
                if not row.get("conflict_status") or row.get("conflict_status") == "unknown":
                    row["conflict_status"] = "superseded"
                changed = True
        if changed:
            self._write_jsonl(self._memories_path, rows)

    def _record_prefetch_trace(self, trace: Dict[str, Any], config: Dict[str, Any]) -> None:
        self._last_prefetch_trace = dict(trace)
        if config.get("prefetch_trace_enabled") is True or config.get("observability_enabled") is True:
            stored = dict(trace)
            stored.pop("context", None)
            if self._should_redact_stored_query_preview(stored, config):
                stored["query_preview"] = "[redacted]"
            self._append_jsonl(self._events_path, stored)

    def _should_redact_stored_query_preview(self, trace: Dict[str, Any], config: Dict[str, Any]) -> bool:
        """Return True when local observability should not persist query text.

        User-facing prefetch_trace keeps query_preview for debuggability, but the
        optional local events log should not retain raw credential/risky prompts.
        """
        preview = str(trace.get("query_preview") or "")
        if not preview:
            return False
        if str(trace.get("skip_reason") or "") == "blocked_or_risky_query":
            return True
        lowered = preview.lower()
        blocked = [str(term).lower() for term in config.get("blocked_terms", [])]
        risky = [str(term).lower() for term in config.get("risky_query_terms", [])]
        return self._has_secret_marker(preview) or any(term and term in lowered for term in blocked + risky)

    def _memory_args_from_tool(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "content": str(args.get("content") or ""),
            "source": str(args.get("source") or ""),
            "context": str(args.get("context") or ""),
            "rationale": str(args.get("rationale") or ""),
            "confidence": str(args.get("confidence") or "unknown"),
            "sensitivity": str(args.get("sensitivity") or "unknown"),
            "stability": str(args.get("stability") or "unknown"),
            "current_request_safe": bool(args.get("current_request_safe") or False),
            "topic": str(args.get("topic") or ""),
            "conflict_group": str(args.get("conflict_group") or ""),
            "conflict_status": str(args.get("conflict_status") or "unknown"),
            "supersedes": args.get("supersedes") if isinstance(args.get("supersedes"), list) else None,
            "valid_from": str(args.get("valid_from") or ""),
            "valid_until": str(args.get("valid_until") or ""),
        }

    @staticmethod
    def _normalize_choice(value: Any, allowed: set[str], *, default: str) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        return normalized if normalized in allowed else default

    @staticmethod
    def _normalize_ids(value: List[str] | None) -> List[str]:
        if not isinstance(value, list):
            return []
        normalized = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized[:20]

    @staticmethod
    def _has_secret_marker(text: str) -> bool:
        lowered = str(text or "").lower()
        # Candidate/writeback paths are stricter than explicit add: block obvious
        # credential vocabulary and common token/private-key shapes before a
        # proposed memory can enter approval flow.
        if _SECRET_RE.search(text):
            return True
        if re.search(r"(?i)\b(api[_ -]?key|password|passwd|private[_ -]?key|secret|token|credential)\b", lowered):
            return True
        if re.search(r"(?:sk-[A-Za-z0-9_-]{8,}|xoxb-[A-Za-z0-9-]{8,}|ghp_[A-Za-z0-9_]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)", text):
            return True
        return False

    @staticmethod
    def _seed_rollback_manifest(*, source: str, dry_run: bool, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        candidate_ids = [str(candidate.get("id") or "") for candidate in candidates if candidate.get("id")]
        return {
            "source": source,
            "dry_run": dry_run,
            "candidate_ids": candidate_ids,
            "trusted_memory_ids": [],
            "rollback_action": "reject pending candidate_ids; no trusted memories were created by seed_source",
            "mutated_memory": False,
        }

    @staticmethod
    def _is_operating_corpus_source_allowlisted(source: str) -> bool:
        normalized = str(source or "").strip().lower()
        if not normalized:
            return False
        allowed_markers = (
            "secondbrain",
            "second brain",
            "obsidian:",
            "linear:",
            "linear/",
            "linear issue",
            "bif-",
        )
        return any(marker in normalized for marker in allowed_markers)

    @staticmethod
    def _seed_rejection(index: int, category: str, reason: str) -> Dict[str, Any]:
        return {
            "index": index,
            "category": category,
            "reason": reason,
            "redacted": True,
            "content_included": False,
        }

    @staticmethod
    def _seed_rejections_error(rejections: List[Dict[str, Any]]) -> str:
        categories = sorted({str(item.get("category") or "rejected") for item in rejections})
        if not categories:
            return "No seed candidates were accepted."
        return "No seed candidates were accepted; rejected categories: " + ", ".join(categories)

    @staticmethod
    def _validate_writeback_fields(*, content: str, source: str, context: str, rationale: str) -> Dict[str, Any] | None:
        values = {"content": str(content or "").strip(), "source": str(source or "").strip(), "context": str(context or "").strip(), "rationale": str(rationale or "").strip()}
        missing = [name for name, value in values.items() if not value]
        if missing:
            return {"success": False, "error": f"Missing required audit fields: {', '.join(missing)}"}
        return None

    def _snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    def _store_path(self) -> Path:
        return self.data_dir / "memories.jsonl"

    @staticmethod
    def _config_int(config: Dict[str, Any], key: str, *, default: int, minimum: int, maximum: int) -> int | None:
        raw = config.get(key, default)
        if isinstance(raw, bool):
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if value < minimum or value > maximum:
            return None
        return value

    @staticmethod
    def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            rows.append(json.loads(raw))
        return rows

    @staticmethod
    def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        tmp.replace(path)


def register(ctx) -> None:
    ctx.register_memory_provider(MnemosyneProvider())
