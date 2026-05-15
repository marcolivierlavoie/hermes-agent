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
            self._root = Path(str(hermes_home)) / "mnemosyne" / "isolated-pilot"
            self._memories_path = self._root / "memories.jsonl"
            self._suppressions_path = self._root / "suppressions.jsonl"
        self._session_id = session_id
        self._root.mkdir(parents=True, exist_ok=True)
        self._memories_path.touch(exist_ok=True)
        self._suppressions_path.touch(exist_ok=True)

    def system_prompt_block(self) -> str:
        return (
            "Mnemosyne isolated-pilot memory is enabled. Use mnemosyne_memory "
            "for auditable add/recall/suppress/unsuppress operations. Suppression "
            "is non-destructive by default and can be rolled back."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        # BIF-565 narrow production enablement: do not inject remembered content
        # automatically. Memories are only user-visible through explicit
        # mnemosyne_memory actions (recall/list/inspect).
        return ""

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
                            "enum": ["add", "recall", "suppress", "unsuppress", "inspect", "list"],
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
            rows.append({"memory": memory, "suppressed": suppressed})
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
            scored.append((score, {"memory": memory, "score": score, "suppressed": suppressed}))
        scored.sort(key=lambda item: (-item[0], item[1]["memory"].get("created_at", "")))
        return [item for _, item in scored[: max(1, min(int(limit or 5), 20))]]

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
