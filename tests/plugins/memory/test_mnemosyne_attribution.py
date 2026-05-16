from __future__ import annotations

import json

from plugins.memory.mnemosyne import MnemosyneProvider


def _provider(tmp_path):
    provider = MnemosyneProvider()
    provider.initialize("test-session", hermes_home=str(tmp_path / "hermes-home"))
    return provider


def _add(provider, **overrides):
    payload = {
        "content": "Linear is the source of truth for Biff OS backlog and closure.",
        "source": "BIF-595 fixture",
        "context": "production answer attribution test",
        "rationale": "The user asked where Biff OS backlog status should come from.",
        "confidence": "high",
        "sensitivity": "non_sensitive",
        "stability": "stable",
        "current_request_safe": True,
        "topic": "linear_source_of_truth",
    }
    payload.update(overrides)
    result = provider.add_memory(**payload)
    assert result["success"] is True
    return result["memory"]


def test_explain_answer_attribution_for_used_memory_id(tmp_path):
    provider = _provider(tmp_path)
    memory = _add(provider)

    result = provider.answer_attribution([memory["id"]], answer_summary="Used to answer backlog authority.")

    assert result["success"] is True
    assert result["used_memory"] is True
    assert result["memory_ids"] == [memory["id"]]
    attribution = result["attributions"][0]
    assert attribution["memory_id"] == memory["id"]
    assert attribution["source"] == "BIF-595 fixture"
    assert attribution["summary"] == "Linear is the source of truth for Biff OS backlog and closure."
    assert attribution["confidence"] == "high"
    assert attribution["sensitivity"] == "non_sensitive"
    assert attribution["redacted"] is False


def test_answer_attribution_empty_when_memory_not_used(tmp_path):
    provider = _provider(tmp_path)
    _add(provider)

    result = provider.answer_attribution([])

    assert result == {
        "success": True,
        "used_memory": False,
        "memory_ids": [],
        "attributions": [],
        "mutated": False,
    }


def test_user_requested_explanation_includes_influence_metadata(tmp_path):
    provider = _provider(tmp_path)
    memory = _add(provider, conflict_group="biff_backlog", conflict_status="active", supersedes=["mn_old"])
    provider.suppress_memory(memory_id=memory["id"], rationale="test stale suppression", source="test")

    result = provider.explain_memory(memory["id"], used_in_answer=True, current_request="Why did that memory influence this?")

    assert result["success"] is True
    explanation = result["memory_influence"]
    assert explanation["memory_id"] == memory["id"]
    assert explanation["used_in_answer"] is True
    assert explanation["source"] == "BIF-595 fixture"
    assert explanation["context"] == "production answer attribution test"
    assert explanation["rationale"] == "The user asked where Biff OS backlog status should come from."
    assert explanation["confidence"] == "high"
    assert explanation["sensitivity"] == "non_sensitive"
    assert explanation["stability"] == "stable"
    assert explanation["current_request_safe"] is True
    assert explanation["suppression"]["suppressed"] is True
    assert explanation["suppression"]["active_suppression_count"] == 1
    assert explanation["conflict"]["conflict_group"] == "biff_backlog"
    assert explanation["conflict"]["conflict_status"] == "active"
    assert explanation["conflict"]["supersedes"] == ["mn_old"]
    assert result["answer_attribution"]["used_memory"] is True


def test_sensitive_local_only_explanation_redacts_summary_and_secret_markers(tmp_path):
    provider = _provider(tmp_path)
    memory = _add(
        provider,
        content="Credential helper is local only. api_key=fixture-secret-value should never be shown.",
        source="local secure note token=fixture-secret-source",
        context="local-only credential workflow password=fixture-secret-password",
        rationale="Explain safe credential handling without exposing secrets.",
        sensitivity="sensitive",
        topic="credentials",
    )

    result = provider.explain_memory(memory["id"], used_in_answer=True)

    dumped = json.dumps(result, sort_keys=True)
    assert "sk-live-abc123" not in dumped
    assert "fixture-secret-password" not in dumped
    assert "fixture-secret-source" not in dumped
    explanation = result["memory_influence"]
    assert explanation["sensitivity"] == "sensitive"
    assert explanation["redacted"] is True
    assert explanation["safe_summary"] == "[redacted: sensitive/local-only memory]"
    assert explanation["source"] == "local secure note token=[redacted]"
    assert explanation["context"] == "local-only credential workflow password=[redacted]"
