"""Conservative auto-harvesting checks for Mnemosyne writeback candidates."""

from __future__ import annotations

import json

from plugins.memory.mnemosyne import MnemosyneProvider


def _provider(tmp_path):
    provider = MnemosyneProvider()
    provider.initialize("harvest-test-session", hermes_home=str(tmp_path))
    return provider


def test_harvest_accepts_durable_preference_and_queues_only(tmp_path):
    provider = _provider(tmp_path)

    result = provider.harvest_candidates(
        content="Marco prefers Biff to keep Slack work in the current chat, not new threads.",
        source="BIF-596 test transcript",
        context="normal Biff work; explicit durable user preference",
        topic="slack operating preference",
    )

    assert result["success"] is True
    assert result["created_candidate_count"] == 1
    assert result["mutated_memory"] is False
    assert provider.list_memories(include_suppressed=True) == []
    candidate = result["candidates"][0]
    assert candidate["content"] == "Marco prefers Biff to keep Slack work in the current chat, not new threads."
    assert candidate["source"] == "BIF-596 test transcript"
    assert candidate["context"] == "normal Biff work; explicit durable user preference"
    assert candidate["rationale"]
    assert candidate["confidence"] in {"high", "medium"}
    assert candidate["sensitivity"] == "non_sensitive"
    assert candidate["stability"] == "stable"
    assert candidate["current_request_safe"] is True
    assert candidate["topic"] == "slack operating preference"


def test_harvest_accepts_stable_environment_fact(tmp_path):
    provider = _provider(tmp_path)

    result = provider.harvest_candidates(
        content="Environment fact: Biff runtime lives at /Users/marco/.hermes/hermes-agent-biff-runtime.",
        source="BIF-596 test transcript",
        context="normal Biff work; stable local project path",
        topic="runtime environment",
    )

    assert result["success"] is True
    assert result["created_candidate_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["content"] == "Biff runtime lives at /Users/marco/.hermes/hermes-agent-biff-runtime."
    assert candidate["stability"] == "stable"
    assert candidate["sensitivity"] == "non_sensitive"
    assert provider.list_memories(include_suppressed=True) == []


def test_harvest_accepts_explicit_user_correction_as_candidate_not_memory(tmp_path):
    provider = _provider(tmp_path)

    result = provider.harvest_candidates(
        content="Correction: Linear is the source of truth for Biff backlog, not Hermes Kanban.",
        source="BIF-596 test transcript",
        context="user corrected durable operating model",
        topic="backlog source of truth",
    )

    assert result["success"] is True
    assert result["created_candidate_count"] == 1
    assert result["mutated_memory"] is False
    assert provider.list_memories(include_suppressed=True) == []
    candidate = result["candidates"][0]
    assert candidate["content"] == "Linear is the source of truth for Biff backlog, not Hermes Kanban."
    assert "correction" in candidate["rationale"]
    assert candidate["confidence"] == "high"
    assert candidate["stability"] == "stable"


def test_harvest_rejects_secrets_temp_progress_and_private_raw_content(tmp_path):
    provider = _provider(tmp_path)
    cases = [
        ("Remember api_key=fixture-secret-value for the vendor account.", False, "secret"),
        ("Temporary update: I finished PR #123 and BIF-596 is in progress, commit abc123 is pending review.", True, "temporary"),
        ("Remember that Marco's spouse had a private medical appointment with raw notes today.", True, "private_sensitive"),
        ("Maybe remember this random one-off debug thought from today.", True, "temporary"),
    ]

    for content, expected_success, expected_category in cases:
        result = provider.harvest_candidates(
            content=content,
            source="BIF-596 rejected fixture",
            context="normal Biff work; should be rejected",
            topic="rejection fixture",
        )
        assert result["success"] is expected_success
        assert result["created_candidate_count"] == 0
        assert result["mutated_memory"] is False
        assert result.get("rejections") or result.get("reason")
        assert result["rejections"][0]["category"] == expected_category

    assert provider.list_candidates(status="all") == []
    assert provider.list_memories(include_suppressed=True) == []


def test_sync_turn_auto_capture_is_config_gated_candidate_only(tmp_path):
    provider = _provider(tmp_path)

    provider.sync_turn(
        "Marco prefers Biff to keep Slack work in the current chat, not new threads.",
        "Acknowledged.",
        session_id="disabled-session",
    )
    assert provider.list_candidates(status="all") == []

    config_path = tmp_path / "mnemosyne" / "config.json"
    config_path.write_text(json.dumps({"l3_auto_capture_enabled": True}), encoding="utf-8")
    provider.sync_turn(
        "Marco prefers Biff to keep Slack work in the current chat, not new threads.",
        "Acknowledged.",
        session_id="enabled-session",
    )

    candidates = provider.list_candidates(status="pending")
    assert len(candidates) == 1
    assert candidates[0]["content"] == "Marco prefers Biff to keep Slack work in the current chat, not new threads."
    assert candidates[0]["source"] == "mnemosyne_sync_turn:enabled-session"
    assert candidates[0]["status"] == "pending"
    assert provider.list_memories(include_suppressed=True) == []


def test_sync_turn_auto_capture_skips_non_primary_context(tmp_path):
    config_path = tmp_path / "mnemosyne" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"l3_auto_capture_enabled": True}), encoding="utf-8")
    provider = MnemosyneProvider()
    provider.initialize("subagent-session", hermes_home=str(tmp_path), agent_context="subagent")

    provider.sync_turn(
        "Marco prefers Biff to keep Slack work in the current chat, not new threads.",
        "Acknowledged.",
    )

    assert provider.list_candidates(status="all") == []
    assert provider.list_memories(include_suppressed=True) == []
