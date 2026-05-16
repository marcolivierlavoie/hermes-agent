"""Safe SecondBrain/Linear operating-corpus seeding into Mnemosyne candidates."""

from __future__ import annotations

from plugins.memory.mnemosyne import MnemosyneProvider


REQUIRED_METADATA = {
    "source",
    "context",
    "rationale",
    "confidence",
    "sensitivity",
    "stability",
    "current_request_safe",
    "topic",
}


def _provider(tmp_path):
    provider = MnemosyneProvider()
    provider.initialize("operating-corpus-seed-test", hermes_home=str(tmp_path))
    return provider


def test_secondbrain_snippet_seed_dry_run_preserves_approval_metadata(tmp_path):
    provider = _provider(tmp_path)

    result = provider.seed_source_candidates(
        source="SecondBrain:Operations/Biff OS.md",
        dry_run=True,
        records=[
            {
                "content": "Biff should keep Slack work in the current chat by default and avoid starting new threads.",
                "source": "SecondBrain:Operations/Biff OS.md#Slack",
                "context": "approved operating-corpus snippet from Marco's SecondBrain vault",
                "rationale": "durable operating convention reviewed for Mnemosyne production seeding",
                "confidence": "high",
                "sensitivity": "non_sensitive",
                "stability": "stable",
                "current_request_safe": True,
                "topic": "slack operating convention",
                "approved": True,
                "valid_from": "2026-05-16",
            }
        ],
    )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["candidate_count"] == 1
    assert result["rejected_count"] == 0
    assert result["mutated_memory"] is False
    assert provider.list_candidates(status="all") == []
    assert provider.list_memories(include_suppressed=True) == []
    candidate = result["candidates"][0]
    assert REQUIRED_METADATA <= set(candidate)
    assert candidate["source"] == "SecondBrain:Operations/Biff OS.md#Slack"
    assert candidate["confidence"] == "high"
    assert candidate["sensitivity"] == "non_sensitive"
    assert candidate["stability"] == "stable"
    assert candidate["current_request_safe"] is True
    assert candidate["topic"] == "slack operating convention"
    assert candidate["valid_from"] == "2026-05-16"
    assert candidate["approval_status"] == "preview_pending_approval"


def test_linear_operating_convention_seed_writes_pending_candidates_only(tmp_path):
    provider = _provider(tmp_path)

    result = provider.seed_source_candidates(
        source="Linear:BIF-597",
        dry_run=False,
        records=[
            {
                "content": "Linear is the source of truth for Biff backlog, priority, status, and closure.",
                "source": "Linear:BIF-597",
                "context": "approved Linear operating convention for Biff OS",
                "rationale": "source-of-truth convention should be approval-reviewed before trusted recall",
                "confidence": "high",
                "sensitivity": "non_sensitive",
                "stability": "stable",
                "current_request_safe": True,
                "topic": "linear source of truth",
                "allowlisted": True,
            }
        ],
    )

    assert result["success"] is True
    assert result["dry_run"] is False
    assert result["candidate_count"] == 1
    assert result["mutated_memory"] is False
    assert provider.list_memories(include_suppressed=True) == []
    stored = provider.list_candidates(status="pending")
    assert len(stored) == 1
    assert stored[0]["status"] == "pending"
    assert stored[0]["content"] == "Linear is the source of truth for Biff backlog, priority, status, and closure."
    assert stored[0]["source"] == "Linear:BIF-597"
    assert stored[0]["confidence"] == "high"
    assert stored[0]["sensitivity"] == "non_sensitive"
    assert stored[0]["stability"] == "stable"
    assert stored[0]["current_request_safe"] is True
    assert result["rollback_manifest"]["trusted_memory_ids"] == []
    assert result["rollback_manifest"]["candidate_ids"] == [stored[0]["id"]]


def test_seed_rejects_and_redacts_risky_snippets_without_persisting_raw_rejections(tmp_path):
    provider = _provider(tmp_path)

    result = provider.seed_source_candidates(
        source="SecondBrain:Operations/Biff OS.md",
        dry_run=False,
        records=[
            {
                "content": "Biff should queue busy Slack messages by default unless Marco uses /steer.",
                "context": "approved operating-corpus snippet",
                "rationale": "durable non-sensitive operating convention",
                "confidence": "high",
                "sensitivity": "non_sensitive",
                "stability": "stable",
                "current_request_safe": True,
                "topic": "slack queueing",
                "approved": True,
            },
            {
                "content": "Remember api_key=sk-test-secret-token for the vendor account.",
                "context": "should be rejected",
                "rationale": "fixture",
                "approved": True,
            },
            {
                "content": "Temporary update: BIF-597 is in progress and pending review today.",
                "context": "should be rejected",
                "rationale": "fixture",
                "approved": True,
            },
            {
                "content": "Marco's spouse has private medical appointment notes in the vault.",
                "context": "should be rejected",
                "rationale": "fixture",
                "approved": True,
            },
        ],
    )

    assert result["success"] is True
    assert result["candidate_count"] == 1
    assert result["rejected_count"] == 3
    assert {item["category"] for item in result["rejections"]} == {"secret", "temporary", "private_sensitive"}
    serialized_rejections = repr(result["rejections"])
    assert "sk-test-secret-token" not in serialized_rejections
    assert "spouse" not in serialized_rejections
    assert "medical appointment" not in serialized_rejections
    assert "BIF-597 is in progress" not in serialized_rejections
    stored = provider.list_candidates(status="pending")
    assert len(stored) == 1
    assert "api_key" not in repr(stored)
    assert provider.list_memories(include_suppressed=True) == []


def test_seed_rejects_unapproved_or_non_allowlisted_sources_and_keeps_candidate_only_invariant(tmp_path):
    provider = _provider(tmp_path)

    result = provider.seed_source_candidates(
        source="random-notes.txt",
        dry_run=False,
        records=[
            {
                "content": "Biff should use an unreviewed random note as memory.",
                "context": "unapproved source",
                "rationale": "fixture",
                "confidence": "high",
                "sensitivity": "non_sensitive",
                "stability": "stable",
                "current_request_safe": True,
                "topic": "bad seed",
            }
        ],
    )

    assert result["success"] is False
    assert result["candidate_count"] == 0
    assert result["rejected_count"] == 1
    assert result["rejections"][0]["category"] in {"source_not_allowlisted", "not_approved_or_allowlisted"}
    assert provider.list_candidates(status="all") == []
    assert provider.list_memories(include_suppressed=True) == []
