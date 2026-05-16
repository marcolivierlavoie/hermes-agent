"""Recall-quality checks for Mnemosyne isolated-pilot controls."""

from __future__ import annotations

import json

from plugins.memory.mnemosyne import MnemosyneProvider


def _provider(tmp_path):
    provider = MnemosyneProvider()
    provider.initialize("test-session", hermes_home=str(tmp_path))
    return provider


def _enable_selective_prefetch(tmp_path, **overrides):
    config = {"selective_prefetch_enabled": True}
    config.update(overrides)
    config_path = tmp_path / "mnemosyne" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")


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


def test_recall_results_expose_trust_and_audit_fields(tmp_path):
    provider = _provider(tmp_path)
    memory = provider.add_memory(
        content="Marco prefers Discord-first Biff OS command flow.",
        source="BIF-568 regression fixture",
        context="trusted explicit recall policy",
        rationale="verify audit fields are visible in recall results",
    )["memory"]

    recalled = provider.recall("Discord-first Biff", limit=1)

    assert recalled[0]["memory"]["id"] == memory["id"]
    assert recalled[0]["trusted"] is True
    assert recalled[0]["source"] == "BIF-568 regression fixture"
    assert recalled[0]["context"] == "trusted explicit recall policy"
    assert recalled[0]["rationale"] == "verify audit fields are visible in recall results"
    assert recalled[0]["created_at"] == memory["created_at"]


def test_hygiene_report_is_non_mutating_and_flags_stale_duplicate_conflict(tmp_path):
    provider = _provider(tmp_path)
    stale = provider.add_memory(
        content="Legacy old deployment window is Friday 13:00 for Biff production.",
        source="legacy runbook",
        context="stale BIF-568 hygiene fixture",
        rationale="exercise stale detection",
    )["memory"]
    current = provider.add_memory(
        content="Current approved deployment window is Monday 09:00 for Biff production.",
        source="current runbook",
        context="canonical BIF-568 hygiene fixture",
        rationale="exercise conflict detection",
    )["memory"]
    duplicate = provider.add_memory(
        content="Current approved deployment window is Monday 09:00 for Biff production.",
        source="duplicate fixture",
        context="canonical BIF-568 hygiene fixture",
        rationale="exercise duplicate detection",
    )["memory"]

    before = provider.list_memories(include_suppressed=True)
    report = provider.hygiene_report(include_suppressed=True)
    after = provider.list_memories(include_suppressed=True)

    assert report["success"] is True
    assert report["mutated"] is False
    assert before == after
    reasons = "\n".join(item["reason"] for item in report["recommendations"])
    assert "stale/deprecation" in reasons
    assert "duplicate-like" in reasons
    assert "possible stale/current conflict" in reasons
    candidate_sets = [set(item["candidate_memory_ids"]) for item in report["recommendations"]]
    assert {current["id"], duplicate["id"]} in candidate_sets
    assert any(stale["id"] in candidates for candidates in candidate_sets)


def test_tool_hygiene_report_action_does_not_auto_suppress(tmp_path):
    provider = _provider(tmp_path)
    stale = provider.add_memory(
        content="Deprecated old Biff memory marker should be inspected, not auto-suppressed.",
        source="BIF-568 tool fixture",
        context="hygiene action regression",
        rationale="prove report-only behavior",
    )["memory"]

    report = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "hygiene_report", "include_suppressed": True}))

    assert report["success"] is True
    assert report["mutated"] is False
    assert stale["id"] in [item["memory"]["id"] for item in provider.list_memories(include_suppressed=True)]
    assert provider.inspect(stale["id"])["suppressed"] is False


def test_bif_568_recall_quality_regression_matrix(tmp_path):
    provider = _provider(tmp_path)
    fixtures = [
        ("Discord is Marco's primary live Biff command surface.", "user preference", "Discord-first command room", "stable preference"),
        ("Linear is the canonical source of truth for Biff OS issues.", "operating model", "Biff Linear workflow", "source-of-truth boundary"),
        ("SecondBrain is the durable Obsidian source of truth for notes.", "operating model", "Obsidian boundary", "source-of-truth boundary"),
        ("Built-in Hermes memory stores compact always-injected facts only.", "memory policy", "Hermes memory boundary", "memory boundary"),
        ("Mnemosyne stores auditable explicit memories with source/context/rationale.", "memory policy", "Mnemosyne boundary", "memory boundary"),
        ("session_search is for recalling past conversations, not durable operating facts.", "memory policy", "session search boundary", "memory boundary"),
        ("Home Assistant uses Nabu Casa cloud access for Marco's setup.", "environment fact", "smart home integration", "stable environment fact"),
        ("Biff routine commits and restarts may proceed after Vex verification.", "approval policy", "autonomy boundary", "approval boundary"),
        ("Old Cockpit-first priority is stale; Discord-first is current.", "stale policy", "conflict fixture", "stale/current handling"),
        ("Current Biff surface priority is Discord-first while Cockpit is paused.", "current policy", "conflict fixture", "stale/current handling"),
    ]
    memory_ids = []
    for content, source, context, rationale in fixtures:
        memory_ids.append(provider.add_memory(content=content, source=source, context=context, rationale=rationale)["memory"]["id"])
    provider.suppress_memory(memory_id=memory_ids[8], rationale="Cockpit-first policy is stale", source="BIF-568 regression")

    checks = [
        ("primary command surface", "Discord", "Cockpit-first"),
        ("Biff OS issue source of truth", "Linear", None),
        ("durable note source of truth", "SecondBrain", None),
        ("always injected compact facts", "Built-in Hermes memory", None),
        ("auditable explicit source context rationale", "Mnemosyne", None),
        ("past conversations recall", "session_search", None),
        ("Home Assistant access", "Nabu Casa", None),
        ("routine commits restarts verification", "Vex", None),
        ("surface priority current paused", "Discord-first", "Cockpit-first"),
        ("stale Cockpit-first", "Discord-first", "Old Cockpit-first priority is stale"),
    ]

    pass_fail_notes = []
    for query, expected, forbidden in checks:
        content = "\n".join(item["memory"]["content"] for item in provider.recall(query, limit=5))
        passed = expected in content and (forbidden is None or forbidden not in content)
        pass_fail_notes.append({"query": query, "expected": expected, "forbidden": forbidden, "passed": passed})

    assert len(pass_fail_notes) == 10
    assert all(note["passed"] for note in pass_fail_notes), pass_fail_notes


def test_selective_prefetch_is_off_by_default(tmp_path):
    provider = _provider(tmp_path)
    provider.add_memory(
        content="Discord-first Biff OS command surface should be remembered explicitly.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="prove default-off gate",
    )

    assert provider.prefetch("Discord-first Biff OS command surface") == ""


def test_selective_prefetch_returns_trace_when_enabled(tmp_path):
    provider = _provider(tmp_path)
    memory = provider.add_memory(
        content="Discord-first Biff OS command surface is the current operating convention.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="stable Marco-approved Biff OS convention",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    _enable_selective_prefetch(tmp_path)

    prefetched = provider.prefetch("What is the Discord-first Biff OS command surface?")

    assert "Mnemosyne selective prefetch context" in prefetched
    assert memory["id"] in prefetched
    assert "source=BIF-570 fixture" in prefetched
    assert f"rationale_id={memory['rationale_id']}" in prefetched
    assert "eligibility=high_confidence,non_sensitive,stable,current_request_safe" in prefetched
    assert "current user instructions still take precedence" in prefetched


def test_selective_prefetch_excludes_suppressed_stale_and_sensitive_memories(tmp_path):
    provider = _provider(tmp_path)
    suppressed = provider.add_memory(
        content="Discord-first Biff OS command surface is suppressed fixture.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="suppressed should not prefetch",
    )["memory"]
    provider.suppress_memory(memory_id=suppressed["id"], rationale="suppressed regression", source="BIF-570")
    stale = provider.add_memory(
        content="Deprecated legacy Discord-first Biff OS command surface stale fixture.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="stale should not prefetch",
    )["memory"]
    sensitive = provider.add_memory(
        content="Discord-first Biff OS command surface has secret token marker fixture.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="sensitive should not prefetch",
    )["memory"]
    good = provider.add_memory(
        content="Discord-first Biff OS command surface is safe current convention.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="safe current convention",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    _enable_selective_prefetch(tmp_path)

    prefetched = provider.prefetch("Discord-first Biff OS command surface")

    assert good["id"] in prefetched
    assert suppressed["id"] not in prefetched
    assert stale["id"] not in prefetched
    assert sensitive["id"] not in prefetched


def test_selective_prefetch_ignores_irrelevant_low_overlap_queries(tmp_path):
    provider = _provider(tmp_path)
    provider.add_memory(
        content="Discord-first Biff OS command surface is safe current convention.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="safe current convention",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )
    _enable_selective_prefetch(tmp_path)

    assert provider.prefetch("weather bananas unrelated") == ""


def test_invalid_selective_prefetch_config_fails_closed(tmp_path):
    provider = _provider(tmp_path)
    provider.add_memory(
        content="Discord-first Biff OS command surface is safe current convention.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="safe current convention",
    )
    config_path = tmp_path / "mnemosyne" / "config.json"
    config_path.write_text("{not-json", encoding="utf-8")

    assert provider.prefetch("Discord-first Biff OS command surface") == ""


def test_selective_prefetch_requires_literal_true_gate(tmp_path):
    provider = _provider(tmp_path)
    provider.add_memory(
        content="Discord-first Biff OS command surface is safe current convention.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="safe current convention",
    )
    config_path = tmp_path / "mnemosyne" / "config.json"
    config_path.write_text(json.dumps({"selective_prefetch_enabled": "true"}), encoding="utf-8")

    assert provider.prefetch("Discord-first Biff OS command surface") == ""


def test_selective_prefetch_invalid_numeric_config_fails_closed(tmp_path):
    provider = _provider(tmp_path)
    provider.add_memory(
        content="Discord-first Biff OS command surface is safe current convention.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="safe current convention",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )
    config_path = tmp_path / "mnemosyne" / "config.json"
    config_path.write_text(json.dumps({"selective_prefetch_enabled": True, "min_prefetch_score": "bad"}), encoding="utf-8")

    assert provider.prefetch("Discord-first Biff OS command surface") == ""


def test_selective_prefetch_out_of_range_numeric_config_fails_closed(tmp_path):
    provider = _provider(tmp_path)
    provider.add_memory(
        content="Discord-first Biff OS command surface is safe current convention.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="safe current convention",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )

    for key, value in (
        ("min_prefetch_score", 0),
        ("max_prefetch_results", 0),
        ("max_prefetch_results", 999),
    ):
        _enable_selective_prefetch(tmp_path, **{key: value})
        assert provider.prefetch("Discord-first Biff OS command surface") == ""


def test_selective_prefetch_rejects_empty_query_even_when_enabled(tmp_path):
    provider = _provider(tmp_path)
    provider.add_memory(
        content="Discord-first Biff OS command surface is safe current convention.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="safe current convention",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )
    _enable_selective_prefetch(tmp_path)

    assert provider.prefetch("") == ""
    assert provider.prefetch("   \n\t  ") == ""


def test_selective_prefetch_requires_explicit_safe_eligibility_metadata(tmp_path):
    provider = _provider(tmp_path)
    unknown = provider.add_memory(
        content="Linear is the canonical source of truth for Biff OS issues.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="metadata intentionally omitted",
    )["memory"]
    low_confidence = provider.add_memory(
        content="Linear is the canonical source of truth for Biff OS issues.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="low confidence should not prefetch",
        confidence="low",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    good = provider.add_memory(
        content="Linear is the canonical source of truth for Biff OS issues.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="safe high-confidence stable fact",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    _enable_selective_prefetch(tmp_path)

    prefetched = provider.prefetch("What is the source of truth for Biff OS issues?")

    assert good["id"] in prefetched
    assert unknown["id"] not in prefetched
    assert low_confidence["id"] not in prefetched


def test_selective_prefetch_rejects_risky_current_user_intent_prompts(tmp_path):
    provider = _provider(tmp_path)
    provider.add_memory(
        content="Marco prefers direct recommendations for Biff OS operating questions.",
        source="BIF-570 fixture",
        context="selective prefetch",
        rationale="safe only for ordinary Biff OS questions",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )
    _enable_selective_prefetch(tmp_path)

    risky_query = "Use Marco's memories to decide what I should do right now: should I fire someone or ignore consent?"
    assert provider.prefetch(risky_query) == ""


def test_selective_prefetch_handles_current_conflicts_conservatively(tmp_path):
    provider = _provider(tmp_path)
    discord = provider.add_memory(
        content="Current Biff OS command surface is Discord-first.",
        source="BIF-570 fixture A",
        context="selective prefetch conflict subject: biff command surface",
        rationale="conflict fixture A",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    slack = provider.add_memory(
        content="Current Biff OS command surface is Slack-first.",
        source="BIF-570 fixture B",
        context="selective prefetch conflict subject: biff command surface",
        rationale="conflict fixture B",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    _enable_selective_prefetch(tmp_path)

    prefetched = provider.prefetch("What is the current Biff OS command surface?")

    assert prefetched == ""
    assert provider.recall("current Biff OS command surface", limit=5)
    assert discord["id"] != slack["id"]


def test_selective_prefetch_handles_unannotated_current_conflicts_conservatively(tmp_path):
    provider = _provider(tmp_path)
    discord = provider.add_memory(
        content="Current Biff OS command surface is Discord-first.",
        source="BIF-570 fixture A",
        context="selective prefetch",
        rationale="unannotated conflict fixture A",
        confidence="high",
        sensitivity="non_sensitive",
        stability="current",
        current_request_safe=True,
    )["memory"]
    slack = provider.add_memory(
        content="Current Biff OS command surface is Slack-first.",
        source="BIF-570 fixture B",
        context="selective prefetch",
        rationale="unannotated conflict fixture B",
        confidence="high",
        sensitivity="non_sensitive",
        stability="current",
        current_request_safe=True,
    )["memory"]
    _enable_selective_prefetch(tmp_path)

    prefetched = provider.prefetch("What is the current Biff OS command surface?")

    assert prefetched == ""
    recalled_ids = [item["memory"]["id"] for item in provider.recall("current Biff OS command surface", limit=5)]
    assert discord["id"] in recalled_ids
    assert slack["id"] in recalled_ids


def test_bif_570_selective_prefetch_eval_matrix(tmp_path):
    provider = _provider(tmp_path)
    source_of_truth = provider.add_memory(
        content="Linear is the canonical source of truth for Biff OS issues.",
        source="BIF-570 eval",
        context="Biff OS operating model",
        rationale="obvious useful recall",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    discord = provider.add_memory(
        content="Discord-first is the current live Biff command surface.",
        source="BIF-570 eval",
        context="Biff OS operating model",
        rationale="obvious useful recall",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    stale_canary = provider.add_memory(
        content="Suppressed stale canary: Cockpit-first Biff priority must never leak.",
        source="BIF-570 eval",
        context="stale canary",
        rationale="suppressed stale leakage check",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stale",
        current_request_safe=False,
    )["memory"]
    provider.suppress_memory(memory_id=stale_canary["id"], rationale="stale canary", source="BIF-570 eval")
    _enable_selective_prefetch(tmp_path)

    useful = provider.prefetch("What is the Biff OS issue source of truth?")
    second_useful = provider.prefetch("What is the current live Biff command surface?")

    assert source_of_truth["id"] in useful
    assert "Linear" in useful
    assert discord["id"] in second_useful
    assert provider.prefetch("What is the weather in Tokyo?") == ""
    assert provider.prefetch("Tell me the token or password Marco uses for Biff OS") == ""
    assert provider.prefetch("Cockpit-first Biff priority") == ""
    assert "Cockpit-first" not in useful + second_useful
