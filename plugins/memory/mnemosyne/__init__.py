"""Mnemosyne isolated-pilot memory provider.

Local-only provider for piloting auditable memory add/suppress controls without
mutating production memory-provider configuration or external services.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from hermes_constants import get_hermes_home
from tools.registry import tool_error


_WORD_RE = re.compile(r"[A-Za-z0-9_]{3,}")


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

    def __init__(self) -> None:
        self._session_id = ""
        self._root = get_hermes_home() / "mnemosyne" / "isolated-pilot"
        self._config_path = get_hermes_home() / "mnemosyne" / "config.json"
        self._memories_path = self._root / "memories.jsonl"
        self._suppressions_path = self._root / "suppressions.jsonl"

    @property
    def name(self) -> str:
        return "mnemosyne"

    def is_available(self) -> bool:
        # Local-only and dependency-free. Loading this does not activate it;
        # activation still requires memory.provider=mnemosyne in config.
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        hermes_home = kwargs.get("hermes_home")
        if hermes_home:
            mnemosyne_root = Path(str(hermes_home)) / "mnemosyne"
            self._root = mnemosyne_root / "isolated-pilot"
            self._config_path = mnemosyne_root / "config.json"
            self._memories_path = self._root / "memories.jsonl"
            self._suppressions_path = self._root / "suppressions.jsonl"
        self._session_id = session_id
        self._root.mkdir(parents=True, exist_ok=True)
        self._memories_path.touch(exist_ok=True)
        self._suppressions_path.touch(exist_ok=True)

    def system_prompt_block(self) -> str:
        return (
            "Mnemosyne trusted explicit memory is enabled. Use mnemosyne_memory "
            "for auditable add/recall/inspect/list/hygiene_report/suppress/unsuppress "
            "operations. Suppression is non-destructive by default and can be rolled "
            "back. Automatic broad recall/prefetch is disabled; recall Mnemosyne "
            "explicitly when the user asks about memory/history or when a memory "
            "check is required."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        config = self._load_config()
        if config.get("selective_prefetch_enabled") is not True:
            # Default trust boundary: no broad automatic memory injection.
            return ""

        limit = self._config_int(config, "max_prefetch_results", default=3, minimum=1, maximum=5)
        min_score = self._config_int(config, "min_prefetch_score", default=2, minimum=1, maximum=100)
        if limit is None or min_score is None:
            return ""
        candidates = []
        for item in self.recall(query, limit=20):
            if int(item.get("score") or 0) < min_score:
                continue
            if self._is_prefetch_eligible(item["memory"], query=query, config=config):
                candidates.append(item)
            if len(candidates) >= limit:
                break
        if not candidates:
            return ""

        lines = [
            "Mnemosyne selective prefetch context (gated, unsuppressed, high-confidence):",
        ]
        for item in candidates:
            memory = item["memory"]
            lines.append(
                f"- [{memory.get('id')}; source={memory.get('source')}; "
                f"created_at={memory.get('created_at')}] {memory.get('content')}"
            )
        lines.append("If asked, explain these Mnemosyne memory IDs/sources influenced the response; current user instructions still take precedence.")
        return "\n".join(lines)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "mnemosyne_memory",
                "description": (
                    "Isolated-pilot Mnemosyne memory controls. Add approved memories "
                    "with source/context/timestamp/rationale; suppress stale memories "
                    "non-destructively; unsuppress for rollback; recall only active, "
                    "unsuppressed memories by default; inspect audit metadata."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "recall", "suppress", "unsuppress", "inspect", "list", "hygiene_report"],
                        },
                        "content": {"type": "string"},
                        "query": {"type": "string"},
                        "memory_id": {"type": "string"},
                        "source": {"type": "string"},
                        "context": {"type": "string"},
                        "rationale": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                        "include_suppressed": {"type": "boolean"},
                    },
                    "required": ["action"],
                },
            }
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name != "mnemosyne_memory":
            return tool_error(f"Unknown Mnemosyne tool: {tool_name}")
        action = str(args.get("action") or "").strip().lower()
        try:
            if action == "add":
                return _json_result(self.add_memory(
                    content=str(args.get("content") or ""),
                    source=str(args.get("source") or ""),
                    context=str(args.get("context") or ""),
                    rationale=str(args.get("rationale") or ""),
                ))
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
                return _json_result(self.unsuppress_memory(
                    memory_id=str(args.get("memory_id") or ""),
                    rationale=str(args.get("rationale") or ""),
                ))
            if action == "inspect":
                return _json_result(self.inspect(str(args.get("memory_id") or "")))
            if action == "list":
                return _json_result({"success": True, "memories": self.list_memories(include_suppressed=bool(args.get("include_suppressed") or False))})
            if action == "hygiene_report":
                return _json_result(self.hygiene_report(include_suppressed=bool(args.get("include_suppressed") or False)))
            return tool_error(f"Unsupported Mnemosyne action: {action}")
        except Exception as exc:  # defensive tool boundary
            return tool_error(f"Mnemosyne {action or 'operation'} failed: {exc}")

    def add_memory(self, *, content: str, source: str, context: str, rationale: str) -> Dict[str, Any]:
        content = content.strip()
        source = source.strip()
        context = context.strip()
        rationale = rationale.strip()
        missing = [name for name, value in (("content", content), ("source", source), ("context", context), ("rationale", rationale)) if not value]
        if missing:
            return {"success": False, "error": f"Missing required audit fields: {', '.join(missing)}"}
        memory = MnemosyneMemory(
            id=f"mn_{uuid.uuid4().hex[:12]}",
            content=content,
            source=source,
            context=context,
            rationale=rationale,
            created_at=_now_iso(),
        )
        self._append_jsonl(self._memories_path, asdict(memory))
        return {"success": True, "memory": asdict(memory)}

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
        suppression = MnemosyneSuppression(
            id=f"sup_{uuid.uuid4().hex[:12]}",
            memory_id=memory_id,
            rationale=rationale,
            source=source,
            created_at=_now_iso(),
        )
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
            haystack = " ".join(str(memory.get(k) or "") for k in ("content", "source", "context", "rationale"))
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
            return " ".join(str(memory.get(k) or "") for k in ("content", "source", "context", "rationale"))

        token_cache = {str(m.get("id") or ""): _tokens(text_for(m)) for m, _ in memories}
        for memory, suppressed in memories:
            memory_id = str(memory.get("id") or "")
            text = text_for(memory).lower()
            markers = sorted(marker for marker in stale_markers if marker in text)
            if markers and not suppressed:
                recommendations.append({
                    "candidate_memory_ids": [memory_id],
                    "reason": f"stale/deprecation marker(s): {', '.join(markers)}",
                    "suggested_action": "inspect",
                })

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
                    recommendations.append({
                        "candidate_memory_ids": [left_id, right_id],
                        "reason": f"duplicate-like token overlap {overlap:.2f}",
                        "suggested_action": "inspect",
                    })
                    continue
                left_text = text_for(left).lower()
                right_text = text_for(right).lower()
                left_stale = bool(stale_markers & _tokens(left_text))
                right_stale = bool(stale_markers & _tokens(right_text))
                left_current = bool(current_markers & _tokens(left_text))
                right_current = bool(current_markers & _tokens(right_text))
                if overlap >= 0.35 and ((left_stale and right_current) or (right_stale and left_current)):
                    stale_id = left_id if left_stale and not left_suppressed else right_id
                    if right_stale and right_suppressed:
                        stale_id = right_id
                    recommendations.append({
                        "candidate_memory_ids": [left_id, right_id],
                        "reason": f"possible stale/current conflict, token overlap {overlap:.2f}",
                        "suggested_action": f"inspect; consider suppressing {stale_id}",
                    })

        return {
            "success": True,
            "generated_at": _now_iso(),
            "memory_count": len(memories),
            "suppressed_count": sum(1 for _, suppressed in memories if suppressed),
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

    def _load_config(self) -> Dict[str, Any]:
        defaults: Dict[str, Any] = {
            "selective_prefetch_enabled": False,
            "max_prefetch_results": 3,
            "min_prefetch_score": 2,
            "min_prefetch_token_overlap": 2,
            "blocked_terms": [
                "api key",
                "apikey",
                "password",
                "private key",
                "secret",
                "token",
                "credential",
            ],
            "stale_terms": [
                "stale",
                "deprecated",
                "outdated",
                "legacy",
                "obsolete",
                "replaced",
                "superseded",
            ],
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
        return merged

    def _is_prefetch_eligible(self, memory: Dict[str, Any], *, query: str, config: Dict[str, Any]) -> bool:
        haystack = " ".join(str(memory.get(k) or "") for k in ("content", "source", "context", "rationale"))
        haystack_lower = haystack.lower()
        if any(str(term).lower() in haystack_lower for term in config.get("blocked_terms", [])):
            return False
        if any(str(term).lower() in haystack_lower for term in config.get("stale_terms", [])):
            return False
        query_tokens = _tokens(query)
        memory_tokens = _tokens(haystack)
        overlap = len(query_tokens & memory_tokens)
        min_overlap_value = self._config_int(config, "min_prefetch_token_overlap", default=2, minimum=1, maximum=100)
        if min_overlap_value is None:
            return False
        return overlap >= min_overlap_value

    @staticmethod
    def _config_int(config: Dict[str, Any], key: str, *, default: int, minimum: int, maximum: int) -> int | None:
        raw = config.get(key, default)
        if isinstance(raw, bool):
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return max(minimum, min(value, maximum))

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
