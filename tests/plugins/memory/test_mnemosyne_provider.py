"""Recall-quality checks for Mnemosyne isolated-pilot controls."""

from __future__ import annotations

import json

from plugins.memory.mnemosyne import MnemosyneProvider


def _provider(tmp_path):
    provider = MnemosyneProvider()
    provider.initialize("test-session", hermes_home=str(tmp_path))
    return provider


def test_add_memory_preserves_audit_metadata_and_is_retrievable(tmp_path):
    provider = _provider(tmp_path)

    added = provider.add_memory(
        content="Marco's pilot codename is blue heron for recall-quality checks.",
        source="BIF-563 test fixture",
        context="isolated pilot approval path",
        rationale="prove approved memory recall and auditability",
    )

    assert added["success"] is True
    memory = added["memory"]
    assert memory["id"].startswith("mn_")
    assert memory["source"] == "BIF-563 test fixture"
    assert memory["context"] == "isolated pilot approval path"
    assert memory["rationale"] == "prove approved memory recall and auditability"
    assert memory["created_at"].endswith("Z")

    recalled = provider.recall("blue heron", limit=3)
    assert [item["memory"]["id"] for item in recalled] == [memory["id"]]

    inspected = provider.inspect(memory["id"])
    assert inspected["success"] is True
    assert inspected["memory"]["source"] == "BIF-563 test fixture"
    assert inspected["memory"]["rationale"] == "prove approved memory recall and auditability"


def test_suppression_hides_stale_memory_and_unsuppress_rolls_back(tmp_path):
    provider = _provider(tmp_path)
    stale = provider.add_memory(
        content="The stale deployment window is Friday at 13:00.",
        source="legacy note",
        context="outdated deployment schedule",
        rationale="fixture stale memory",
    )["memory"]
    approved = provider.add_memory(
        content="The approved deployment window is Monday at 09:00.",
        source="current plan",
        context="updated deployment schedule",
        rationale="fixture approved memory",
    )["memory"]

    suppression = provider.suppress_memory(
        memory_id=stale["id"],
        rationale="Friday deployment window is stale",
        source="BIF-563 test",
    )
    assert suppression["success"] is True
    assert suppression["suppression"]["active"] is True

    recalled = provider.recall("deployment window Friday Monday", limit=10)
    recalled_ids = [item["memory"]["id"] for item in recalled]
    assert approved["id"] in recalled_ids
    assert stale["id"] not in recalled_ids

    inspected_stale = provider.inspect(stale["id"])
    assert inspected_stale["suppressed"] is True
    assert inspected_stale["suppressions"][0]["rationale"] == "Friday deployment window is stale"

    rollback = provider.unsuppress_memory(
        memory_id=stale["id"],
        rationale="rollback requested after verifying stale marker behavior",
    )
    assert rollback["success"] is True

    recalled_after_rollback = provider.recall("Friday deployment window", limit=10)
    assert stale["id"] in [item["memory"]["id"] for item in recalled_after_rollback]
    inspected_after_rollback = provider.inspect(stale["id"])
    assert inspected_after_rollback["suppressed"] is False
    assert inspected_after_rollback["suppressions"][0]["unsuppress_rationale"] == "rollback requested after verifying stale marker behavior"


def test_tool_surface_enforces_audit_fields_and_defaults_to_unsuppressed_recall(tmp_path):
    provider = _provider(tmp_path)

    missing = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "add", "content": "no audit"}))
    assert missing["success"] is False
    assert "source" in missing["error"]
    assert "context" in missing["error"]
    assert "rationale" in missing["error"]

    added = json.loads(provider.handle_tool_call(
        "mnemosyne_memory",
        {
            "action": "add",
            "content": "Neptune kettle is the pilot-only stale marker.",
            "source": "tool test",
            "context": "bounded recall fixture",
            "rationale": "exercise tool audit path",
        },
    ))["memory"]
    json.loads(provider.handle_tool_call(
        "mnemosyne_memory",
        {
            "action": "suppress",
            "memory_id": added["id"],
            "source": "tool test",
            "rationale": "hide stale marker by default",
        },
    ))

    default_recall = json.loads(provider.handle_tool_call(
        "mnemosyne_memory",
        {"action": "recall", "query": "Neptune kettle", "limit": 10},
    ))
    assert default_recall["memories"] == []

    audit_recall = json.loads(provider.handle_tool_call(
        "mnemosyne_memory",
        {"action": "recall", "query": "Neptune kettle", "include_suppressed": True, "limit": 10},
    ))
    assert audit_recall["memories"][0]["memory"]["id"] == added["id"]
    assert audit_recall["memories"][0]["suppressed"] is True
