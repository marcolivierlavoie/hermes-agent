"""Explicit memory/profile tiering for Biff Discord turns.

The tier decision is intentionally cheap and deterministic. It controls how much
Mnemosyne-derived context Biff's hot-context capsule injects before the model
runs; tool exposure is still handled by the turn toolset planner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BiffMemoryTierDecision:
    tier: str
    reason: str
    max_snapshot_chars: int
    allow_mnemosyne_snapshot: bool
    require_memory_tool_lane: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "reason": self.reason,
            "max_snapshot_chars": self.max_snapshot_chars,
            "allow_mnemosyne_snapshot": self.allow_mnemosyne_snapshot,
            "require_memory_tool_lane": self.require_memory_tool_lane,
        }


_MEMORY_WRITE_RE = re.compile(
    r"\b(?:remember\s+this|save\s+this\s+(?:to|in)\s+(?:memory|mnemosyne)|"
    r"add\s+this\s+to\s+(?:memory|mnemosyne)|make\s+a\s+memory|queue\s+(?:a\s+)?memory\s+candidate)\b",
    re.IGNORECASE,
)
_MEMORY_RECALL_RE = re.compile(
    r"\b(?:what\s+do\s+you\s+remember|what\s+do\s+we\s+know|recall\s+(?:memory|mnemosyne)|"
    r"memory\s+(?:check|lookup|recall)|mnemosyne\s+(?:recall|memory|candidate)|"
    r"preference|preferences|profile|remember\s+about)\b",
    re.IGNORECASE,
)
_HISTORY_RE = re.compile(
    r"\b(?:history|past\s+(?:conversation|thread|session)|previous\s+(?:conversation|thread|session)|"
    r"transcript|session\s+search|what\s+did\s+we\s+(?:say|decide|discuss)|where\s+did\s+we\s+leave\s+off)\b",
    re.IGNORECASE,
)
_STABLE_CONTEXT_RE = re.compile(
    r"\b(?:BIF-\d+|K-\d+|biff\s*os|kanban|story|card|issue|runtime|gateway|"
    r"role\s+handoff|forge|vex|quill|ranger|safety|policy|consent|source\s+of\s+truth)\b",
    re.IGNORECASE,
)


def classify_biff_memory_tier(message: Any, plan: Any = None) -> BiffMemoryTierDecision:
    """Classify how much memory context a Biff turn should receive.

    Tiers:
    - ``no-memory``: casual/direct or sensitive surfaces where memory is not needed.
    - ``compact-memory``: stable Biff policy/context snapshot, bounded and LLM-free.
    - ``normal-memory``: explicit memory/preference/writeback work; expose memory lane.
    - ``full-memory-history``: history/transcript/session recovery work; include a larger snapshot.
    """

    body = " ".join(str(message or "").split())
    lowered_runtime = str(getattr(plan, "runtime", "") or "").strip()
    lowered_profile = str(getattr(plan, "toolset_profile", "") or "").strip()
    lowered_action = str(getattr(plan, "action", "") or "").strip()

    if _HISTORY_RE.search(body) or lowered_runtime == "context_resume" or lowered_profile == "resume":
        return BiffMemoryTierDecision(
            "full-memory-history",
            "history/session recovery requires durable memory plus session context",
            1800,
            True,
            require_memory_tool_lane=True,
        )
    if _MEMORY_WRITE_RE.search(body):
        return BiffMemoryTierDecision(
            "normal-memory",
            "explicit remember-this/writeback request needs Mnemosyne candidate flow",
            1200,
            True,
            require_memory_tool_lane=True,
        )
    if _MEMORY_RECALL_RE.search(body) or lowered_runtime == "memory_lookup" or lowered_profile == "memory":
        return BiffMemoryTierDecision(
            "normal-memory",
            "explicit memory/preference lookup needs Mnemosyne recall tools",
            1200,
            True,
            require_memory_tool_lane=True,
        )
    if lowered_profile in {"mental_health", "dashboard", "none"} or (
        lowered_action == "answer_now" and lowered_runtime == "direct_answer"
    ):
        return BiffMemoryTierDecision(
            "no-memory",
            "direct/casual or private routing surface does not need memory prefetch",
            0,
            False,
        )
    if _STABLE_CONTEXT_RE.search(body) or lowered_runtime in {
        "workflow",
        "continuation",
        "tool_access_recovery",
        "specialist_work",
        "kanban_read",
        "kanban_admin",
    }:
        return BiffMemoryTierDecision(
            "compact-memory",
            "Biff OS/safety/workflow turn needs compact stable policy context",
            650,
            True,
        )
    return BiffMemoryTierDecision(
        "compact-memory",
        "default Biff command-room context keeps compact stable memory available",
        650,
        True,
    )


__all__ = ["BiffMemoryTierDecision", "classify_biff_memory_tier"]
