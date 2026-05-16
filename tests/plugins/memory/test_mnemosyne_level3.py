"""Level 3 Mnemosyne product-contract and writeback controls."""

from __future__ import annotations

import json

from plugins.memory.mnemosyne import MNEMOSYNE_PRODUCT_CONTRACT, MnemosyneProvider


def _provider(tmp_path):
    provider = MnemosyneProvider()
    provider.initialize("level3-test", hermes_home=str(tmp_path))
    return provider


def _enable_selective_prefetch(tmp_path, **overrides):
    config = {"selective_prefetch_enabled": True}
    config.update(overrides)
    config_path = tmp_path / "mnemosyne" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")


def test_contract_exposes_level3_safety_and_config_defaults(tmp_path):
    provider = _provider(tmp_path)

    result = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "contract"}))

    assert result["success"] is True
    assert result["contract"] == MNEMOSYNE_PRODUCT_CONTRACT
    assert "No bulk imports" in result["contract"]
    assert "No secrets" in result["contract"]
    assert "Rollout/rollback helpers return manifests only" in result["contract"]
    assert result["config"]["candidate_queue_enabled"] is True
    assert result["config"]["selective_prefetch_enabled"] is False


def test_candidate_queue_approves_and_rejects_without_implicit_memory_mutation(tmp_path):
    provider = _provider(tmp_path)

    candidate = provider.add_candidate(
        content="Linear remains the Biff OS source of truth for issues.",
        source="BIF-573 fixture",
        context="writeback queue",
        rationale="stage explicit writeback before approval",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["candidate"]
    rejected = provider.add_candidate(
        content="Temporary scratch preference should not become memory.",
        source="BIF-573 fixture",
        context="writeback queue",
        rationale="exercise rejection",
    )["candidate"]

    assert provider.recall("Linear source truth issues") == []
    assert [item["id"] for item in provider.list_candidates()] == [candidate["id"], rejected["id"]]

    approved = provider.approve_candidate(candidate_id=candidate["id"], rationale="approved in test")
    reject = provider.reject_candidate(candidate_id=rejected["id"], rationale="not stable enough")

    assert approved["success"] is True
    assert approved["memory"]["id"].startswith("mn_")
    assert reject["success"] is True
    assert reject["mutated_memory"] is False
    assert provider.list_candidates(status="pending") == []
    assert provider.list_candidates(status="approved")[0]["approved_memory_id"] == approved["memory"]["id"]
    assert provider.list_candidates(status="rejected")[0]["decision_rationale"] == "not stable enough"
    assert provider.recall("Biff OS source truth issues")[0]["memory"]["id"] == approved["memory"]["id"]


def test_writeback_rejects_likely_secret_content(tmp_path):
    provider = _provider(tmp_path)

    rejected = provider.add_candidate(
        content="api_key = ***",
        source="BIF-574 fixture",
        context="secret safety",
        rationale="should fail closed",
    )

    assert rejected["success"] is False
    assert "secret" in rejected["error"].lower()
    assert provider.list_candidates(status="all") == []


def test_candidate_queue_rejects_secret_markers_in_audit_fields(tmp_path):
    provider = _provider(tmp_path)

    rejected = provider.add_candidate(
        content="Safe public preference.",
        source="BIF-574 fixture token=abc12345",
        context="secret marker in audit field",
        rationale="should fail closed before queueing",
    )

    assert rejected["success"] is False
    assert "secret" in rejected["error"].lower()
    assert provider.list_candidates(status="all") == []


def test_candidate_writeback_enabled_false_disables_candidate_queue_alias(tmp_path):
    provider = _provider(tmp_path)
    config_path = tmp_path / "mnemosyne" / "config.json"
    config_path.write_text(json.dumps({"candidate_writeback_enabled": False}), encoding="utf-8")

    rejected = provider.add_candidate(
        content="Linear remains the Biff OS source of truth for issues.",
        source="BIF-574 fixture",
        context="writeback disable alias",
        rationale="candidate_writeback_enabled false should fail closed",
    )

    assert rejected["success"] is False
    assert "disabled" in rejected["error"].lower()
    assert provider.list_candidates(status="all") == []


def test_candidate_queue_enabled_false_still_disables_candidate_queue(tmp_path):
    provider = _provider(tmp_path)
    config_path = tmp_path / "mnemosyne" / "config.json"
    config_path.write_text(json.dumps({"candidate_queue_enabled": False}), encoding="utf-8")

    rejected = provider.add_candidate(
        content="Linear remains the Biff OS source of truth for issues.",
        source="BIF-574 fixture",
        context="writeback disable canonical key",
        rationale="candidate_queue_enabled false should fail closed",
    )

    assert rejected["success"] is False
    assert "disabled" in rejected["error"].lower()


def test_supersession_and_conflict_metadata_are_exposed_and_reported(tmp_path):
    provider = _provider(tmp_path)
    old = provider.add_memory(
        content="Old command surface is Cockpit-first for Biff.",
        source="BIF-575 old",
        context="supersession fixture",
        rationale="legacy fact",
        topic="command surface",
        conflict_group="biff command surface",
        conflict_status="active",
    )["memory"]
    new = provider.add_memory(
        content="Current command surface is Discord-first for Biff.",
        source="BIF-575 new",
        context="supersession fixture",
        rationale="current fact supersedes old fact",
        topic="command surface",
        conflict_group="biff command surface",
        conflict_status="resolved",
        supersedes=[old["id"]],
    )["memory"]

    inspected_old = provider.inspect(old["id"])["memory"]
    recalled_new = provider.recall("current command surface Discord", limit=1)[0]
    report = provider.hygiene_report(include_suppressed=True)

    assert inspected_old["superseded_by"] == new["id"]
    assert recalled_new["conflict_group"] == "biff command surface"
    assert recalled_new["supersedes"] == [old["id"]]
    assert any("supersession" in item["reason"] for item in report["recommendations"])
    assert report["mutated"] is False


def test_prefetch_conflict_detection_skips_unannotated_conflicting_memories(tmp_path):
    provider = _provider(tmp_path)
    provider.add_memory(
        content="Biff command surface is Discord.",
        source="BIF-575 conflict fixture",
        context="unannotated conflict candidate",
        rationale="current candidate without explicit conflict metadata",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )
    provider.add_memory(
        content="Biff command surface is Cockpit.",
        source="BIF-575 conflict fixture",
        context="unannotated conflict candidate",
        rationale="conflicting candidate without explicit conflict metadata",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )
    _enable_selective_prefetch(tmp_path, max_prefetch_results=5, min_prefetch_token_overlap=2)

    trace = provider.prefetch_trace("What is the Biff command surface?")
    explicit = provider.recall("Biff command surface", limit=5)

    assert trace["injected"] is False
    assert trace["skip_reason"] == "conflict_detected"
    assert len(trace["memory_ids"]) == 2
    assert {item["memory"]["content"] for item in explicit} == {"Biff command surface is Discord.", "Biff command surface is Cockpit."}


def test_stale_current_pair_prefetches_clear_current_winner(tmp_path):
    provider = _provider(tmp_path)
    stale = provider.add_memory(
        content="Stale command surface was Cockpit-first for Biff.",
        source="BIF-575 stale fixture",
        context="stale/current pair",
        rationale="legacy stale fact",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stale",
        current_request_safe=True,
    )["memory"]
    current = provider.add_memory(
        content="Current command surface is Discord-first for Biff.",
        source="BIF-575 current fixture",
        context="current pair fixture",
        rationale="current fact replaces earlier fact",
        confidence="high",
        sensitivity="non_sensitive",
        stability="current",
        current_request_safe=True,
        supersedes=[stale["id"]],
    )["memory"]
    _enable_selective_prefetch(tmp_path, max_prefetch_results=5, min_prefetch_token_overlap=2)

    trace = provider.prefetch_trace("What is the Biff command surface?")

    assert trace["injected"] is True
    assert trace["memory_ids"] == [current["id"]]
    assert any(item["id"] == stale["id"] and item["skip_reason"] in {"superseded", "stability_not_stable_or_current"} for item in trace["candidate_traces"])


def test_hygiene_reports_duplicate_like_memories_and_suppression_rollback(tmp_path):
    provider = _provider(tmp_path)
    first = provider.add_memory(
        content="Linear remains the Biff OS source of truth for issue status.",
        source="BIF-575 duplicate fixture",
        context="duplicate-like fixture",
        rationale="first duplicate-like memory",
    )["memory"]
    second = provider.add_memory(
        content="Linear remains the Biff OS source of truth for issue statuses.",
        source="BIF-575 duplicate fixture",
        context="duplicate-like fixture",
        rationale="second duplicate-like memory",
    )["memory"]

    report_before = provider.hygiene_report(include_suppressed=True)
    suppressed = provider.suppress_memory(memory_id=second["id"], rationale="mistaken duplicate suppression test", source="BIF-575 test")
    hidden = provider.recall("issue statuses", include_suppressed=False)
    restored = provider.unsuppress_memory(memory_id=second["id"], rationale="rollback mistaken suppression test")
    visible = provider.recall("issue statuses", include_suppressed=False)

    assert any(set(item["candidate_memory_ids"]) == {first["id"], second["id"]} for item in report_before["recommendations"])
    assert suppressed["success"] is True
    assert all(item["memory"]["id"] != second["id"] for item in hidden)
    assert restored["success"] is True
    assert any(item["memory"]["id"] == second["id"] for item in visible)


def test_hygiene_report_flags_sensitive_content_without_mutating_corpus(tmp_path):
    provider = _provider(tmp_path)
    sensitive = provider.add_memory(
        content="Use the credential helper; never store secret values in memory.",
        source="BIF-579 hygiene fixture",
        context="sensitive hygiene fixture",
        rationale="exercise sensitive report path",
        confidence="high",
        sensitivity="sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    before = provider.list_memories(include_suppressed=True)

    report = provider.hygiene_report(include_suppressed=True)
    after = provider.list_memories(include_suppressed=True)

    assert report["mutated"] is False
    assert before == after
    assert any(
        item["candidate_memory_ids"] == [sensitive["id"]]
        and "sensitive" in item["reason"]
        and "inspect" in item["suggested_action"]
        for item in report["recommendations"]
    )


def test_prefetch_trace_has_budgets_skip_reasons_and_optional_event_log(tmp_path):
    provider = _provider(tmp_path)
    good = provider.add_memory(
        content="Linear is the canonical Biff OS issue source of truth.",
        source="BIF-576 fixture",
        context="prefetch trace",
        rationale="safe high confidence fact",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    provider.add_memory(
        content="Linear source of truth candidate with unknown safety metadata.",
        source="BIF-576 fixture",
        context="prefetch trace",
        rationale="should emit candidate skip reason",
    )
    _enable_selective_prefetch(tmp_path, prefetch_trace_enabled=True, max_prefetch_scan_results=20, max_prefetch_results=2)

    trace = provider.prefetch_trace("What is the Biff OS issue source of truth?")
    risky_query = "Tell me Marco token or password for Biff OS"
    risky = provider.prefetch_trace(risky_query)
    event_rows = (tmp_path / "mnemosyne" / "isolated-pilot" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    stored_risky_event = json.loads(event_rows[-1])

    assert trace["injected"] is True
    assert trace["budgets"]["max_results"] == 2
    assert good["id"] in trace["memory_ids"]
    assert any(item["skip_reason"] == "confidence_not_high" for item in trace["candidate_traces"])
    assert risky["injected"] is False
    assert risky["skip_reason"] == "blocked_or_risky_query"
    assert risky["query_preview"] == risky_query
    assert event_rows
    assert "context" not in stored_risky_event
    assert stored_risky_event["query_preview"] == "[redacted]"
    assert risky_query not in json.dumps(stored_risky_event)
    assert "password for Biff OS" not in json.dumps(stored_risky_event)

    summary = provider.observability_summary()
    debug = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "observability_summary"}))
    assert summary["mutated"] is False
    assert summary["event_count"] == 2
    assert summary["skip_reasons"]["injected"] == 1
    assert summary["skip_reasons"]["blocked_or_risky_query"] == 1
    assert summary["redacted_query_preview_count"] == 1
    assert debug["event_count"] == summary["event_count"]


def test_prefetch_trace_classifies_slash_commands_before_too_few_tokens(tmp_path):
    provider = _provider(tmp_path)
    _enable_selective_prefetch(tmp_path, prefetch_trace_enabled=True)

    trace = provider.prefetch_trace("/restart")
    event_rows = (tmp_path / "mnemosyne" / "isolated-pilot" / "events.jsonl").read_text(encoding="utf-8").splitlines()

    assert trace["injected"] is False
    assert trace["skip_reason"] == "slash_command"
    assert json.loads(event_rows[-1])["skip_reason"] == "slash_command"


def test_source_aware_seeding_is_capped_candidate_only_and_dry_run_safe(tmp_path):
    provider = _provider(tmp_path)

    dry_run = provider.seed_source_candidates(
        source="BIF-577 seed fixture",
        records=[{"content": "Seeded fact candidate for Biff OS.", "context": "seed test"}],
        dry_run=True,
    )
    assert dry_run["success"] is True
    assert dry_run["dry_run"] is True
    assert dry_run["rollback_manifest"]["candidate_ids"] == []
    assert dry_run["rollback_manifest"]["mutated_memory"] is False
    assert provider.list_candidates(status="all") == []

    seeded = provider.seed_source_candidates(
        source="BIF-577 seed fixture",
        records=[{"content": "Second seeded fact candidate for Biff OS.", "context": "seed test"}],
        dry_run=False,
    )
    bulk = provider.seed_source_candidates(
        source="BIF-577 seed fixture",
        records=[{"content": f"fact {idx}", "context": "bulk"} for idx in range(11)],
        dry_run=False,
    )

    assert seeded["success"] is True
    assert seeded["mutated_memory"] is False
    assert seeded["rollback_manifest"]["candidate_ids"] == [seeded["candidates"][0]["id"]]
    assert seeded["rollback_manifest"]["trusted_memory_ids"] == []
    assert len(provider.list_candidates()) == 1
    assert provider.recall("Second seeded fact candidate") == []
    assert bulk["success"] is False
    assert "Refusing bulk seed" in bulk["error"]


def test_source_aware_seeding_rejects_secret_content_before_queueing(tmp_path):
    provider = _provider(tmp_path)

    seeded = provider.seed_source_candidates(
        source="BIF-577 seed fixture",
        records=[{"content": "password: fixture-secret-value", "context": "seed secret test"}],
        dry_run=False,
    )

    assert seeded["success"] is False
    assert "secret" in seeded["error"].lower()
    assert provider.list_candidates(status="all") == []


def test_rollout_manifest_is_non_mutating_and_profile_scoped(tmp_path):
    provider = _provider(tmp_path)

    manifest = provider.rollout_manifest()
    via_tool = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "rollout_manifest"}))

    assert manifest["success"] is True
    assert manifest["mutated"] is False
    assert manifest["non_mutating"] is True
    assert str(tmp_path) in manifest["config_path"]
    assert "full_disable" in manifest["rollback_modes"]
    assert via_tool["config_path"] == manifest["config_path"]


def test_phase4_digest_separates_decisions_from_fyi_without_raw_sensitive_content(tmp_path):
    provider = _provider(tmp_path)
    provider.add_candidate(
        content="Stable non-sensitive candidate awaiting review.",
        source="BIF-588 digest fixture",
        context="operator review",
        rationale="exercise pending decision summary",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )
    sensitive = provider.add_memory(
        content="Use credential helper; never store secret values in memory.",
        source="BIF-588 digest fixture",
        context="sensitive digest fixture",
        rationale="exercise redacted hygiene summary",
        confidence="high",
        sensitivity="sensitive",
        stability="stable",
        current_request_safe=False,
    )["memory"]

    digest = provider.memory_digest()
    via_tool = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "memory_digest"}))

    assert digest["success"] is True
    assert digest["mutated"] is False
    assert digest["needs_decision"]["pending_candidate_count"] == 1
    assert digest["needs_decision"]["hygiene_recommendation_count"] >= 1
    assert sensitive["id"] in json.dumps(digest)
    assert "never store secret values" not in json.dumps(digest)
    assert via_tool["needs_decision"] == digest["needs_decision"]


def test_phase4_recall_policy_exposes_authority_and_attribution_contract(tmp_path):
    provider = _provider(tmp_path)

    policy = provider.recall_policy()
    via_tool = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "recall_policy"}))

    assert policy["success"] is True
    assert policy["mutated"] is False
    assert policy["authority_order"][0] == "current_user_instruction"
    assert "mnemosyne_trusted_memory" in policy["authority_order"]
    assert policy["candidate_memory_policy"] == "never inject; cite only as untrusted pending signal during explicit review"
    assert "why_did_you_remember_that" in policy["attribution"]
    assert via_tool["authority_order"] == policy["authority_order"]


def test_phase4_semantic_quality_gates_fail_closed_until_corpus_and_review_loop_are_clean(tmp_path):
    provider = _provider(tmp_path)
    provider.add_candidate(
        content="Pending seed candidate blocks semantic expansion.",
        source="BIF-590 quality fixture",
        context="quality gate",
        rationale="exercise pending candidate gate",
    )
    provider.add_memory(
        content="Unknown metadata should block semantic expansion.",
        source="BIF-590 quality fixture",
        context="quality gate",
        rationale="exercise hygiene gate",
    )

    gates = provider.semantic_quality_gates()
    via_tool = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "semantic_quality_gates"}))

    assert gates["success"] is True
    assert gates["mutated"] is False
    assert gates["semantic_recall_allowed"] is False
    assert "no_pending_candidates" in gates["failed_gates"]
    assert "clean_hygiene_report" in gates["failed_gates"]
    assert gates["metrics"]["pending_candidate_count"] == 1
    assert via_tool["semantic_recall_allowed"] is False


def test_phase5_full_production_features_are_tool_accessible_and_safe(tmp_path):
    provider = _provider(tmp_path)
    memory = provider.add_memory(
        content="Marco defines Mnemosyne production as live full-feature usage in Biff, not a partial subset.",
        source="BIF-592 fixture",
        context="production definition",
        rationale="exercise production recall and attribution",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
        topic="mnemosyne production",
    )["memory"]
    provider.prefetch_trace("What is Marco's Mnemosyne production definition?")

    eval_pack = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "production_eval_pack"}))
    eval_result = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "run_production_eval"}))
    explain = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "explain_memory", "memory_id": memory["id"]}))
    semantic = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "semantic_recall", "query": "full feature production"}))

    assert eval_pack["success"] is True
    assert eval_pack["case_count"] >= 8
    assert {case["expected_behavior"] for case in eval_pack["cases"]} >= {"inject", "skip", "verify", "qualify"}
    assert eval_result["success"] is True
    assert eval_result["metrics"]["case_count"] == eval_pack["case_count"]
    assert set(eval_result["metrics"]) >= {"precision", "false_positive_rate", "stale_recall_rate", "conflict_skip_rate", "user_correction_rate"}
    assert explain["success"] is True
    assert explain["memory_id"] == memory["id"]
    assert explain["why_did_you_remember_that"]["source"] == "BIF-592 fixture"
    assert "production as live full-feature" not in json.dumps(explain)
    assert semantic["success"] is True
    assert semantic["semantic_recall_allowed"] is True
    assert semantic["results"][0]["memory"]["id"] == memory["id"]


def test_phase5_harvest_candidates_and_correction_supersession_loop(tmp_path):
    provider = _provider(tmp_path)
    old = provider.add_memory(
        content="Old Mnemosyne production means partial safe subset.",
        source="old fixture",
        context="stale production definition",
        rationale="legacy definition",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stale",
        current_request_safe=True,
        topic="mnemosyne production",
        conflict_group="mnemosyne production definition",
        conflict_status="active",
    )["memory"]

    harvested = json.loads(provider.handle_tool_call("mnemosyne_memory", {
        "action": "harvest_candidates",
        "content": "Correction: Mnemosyne production means live full-feature use, not a partial subset.",
        "source": "BIF-592 user correction",
        "context": "Marco correction",
        "topic": "mnemosyne production",
    }))
    rejected = json.loads(provider.handle_tool_call("mnemosyne_memory", {
        "action": "harvest_candidates",
        "content": "password: fixture-secret-value",
        "source": "bad fixture",
        "context": "secret rejection",
    }))
    correction = json.loads(provider.handle_tool_call("mnemosyne_memory", {
        "action": "apply_correction",
        "content": "Mnemosyne production means live full-feature use, not a partial subset.",
        "source": "BIF-592 user correction",
        "context": "Marco correction",
        "rationale": "Marco corrected the production bar.",
        "memory_id": old["id"],
        "topic": "mnemosyne production",
        "conflict_group": "mnemosyne production definition",
    }))

    assert harvested["success"] is True
    assert harvested["created_candidate_count"] == 1
    assert harvested["candidates"][0]["status"] == "pending"
    assert rejected["success"] is False
    assert "secret" in rejected["error"].lower()
    assert correction["success"] is True
    assert correction["candidate"]["supersedes"] == [old["id"]]
    assert correction["mutated_memory"] is False
    assert provider.inspect(old["id"])["memory"].get("superseded_by", "") == ""
    assert provider.inspect(old["id"])["suppressed"] is False


def test_phase5_decision_digest_is_silent_when_no_action_needed_and_actionable_when_pending(tmp_path):
    provider = _provider(tmp_path)

    quiet = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "discord_decision_digest"}))
    provider.add_candidate(
        content="Marco prefers full-feature Mnemosyne production.",
        source="BIF-592 fixture",
        context="pending decision",
        rationale="durable correction candidate",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )
    sensitive = provider.add_memory(
        content="Sensitive operational rule body must not appear in digest.",
        source="BIF-598 sensitive fixture",
        context="digest hygiene",
        rationale="exercise hygiene ID-only rendering",
        confidence="high",
        sensitivity="sensitive",
        stability="stable",
        current_request_safe=False,
    )["memory"]
    conflict = provider.add_memory(
        content="Conflicting operational rule body must not appear in digest.",
        source="BIF-598 conflict fixture",
        context="digest conflict",
        rationale="exercise conflict ID-only rendering",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
        conflict_group="digest conflict fixture",
        conflict_status="active",
    )["memory"]
    actionable = json.loads(provider.handle_tool_call("mnemosyne_memory", {"action": "discord_decision_digest"}))

    assert quiet["success"] is True
    assert quiet["should_notify"] is False
    assert quiet["message"] == ""
    assert actionable["success"] is True
    assert actionable["should_notify"] is True
    assert "Marco decision needed" in actionable["message"]
    assert "candidate" in actionable["message"].lower()
    assert sensitive["id"] in actionable["message"]
    assert conflict["id"] in actionable["message"]
    assert "full-feature Mnemosyne production" not in actionable["message"]
    assert "Sensitive operational rule body" not in actionable["message"]
    assert "Conflicting operational rule body" not in actionable["message"]


def test_phase5_apply_correction_rejects_secrets_before_trusted_write(tmp_path):
    provider = _provider(tmp_path)
    old = provider.add_memory(
        content="Safe old fact.",
        source="old fixture",
        context="old correction target",
        rationale="legacy fact",
    )["memory"]

    rejected = json.loads(provider.handle_tool_call("mnemosyne_memory", {
        "action": "apply_correction",
        "content": "password: fixture-secret-value",
        "source": "BIF-592 security fixture",
        "context": "secret correction rejection",
        "rationale": "should fail closed",
        "memory_id": old["id"],
    }))

    assert rejected["success"] is False
    assert "secret" in rejected["error"].lower()
    assert provider.inspect(old["id"])["memory"].get("superseded_by", "") == ""


def test_phase5_semantic_recall_filters_memories_that_fail_prefetch_safety(tmp_path):
    provider = _provider(tmp_path)
    unsafe = provider.add_memory(
        content="Unsafe decoy should never be returned.",
        source="semantic full feature production unsafe fixture",
        context="semantic safety",
        rationale="should not be returned",
        confidence="low",
        sensitivity="non_sensitive",
        stability="temporary",
        current_request_safe=False,
    )["memory"]
    safe = provider.add_memory(
        content="Safe semantic candidate for full feature production.",
        source="safe fixture",
        context="semantic safety",
        rationale="should be returned",
        confidence="high",
        sensitivity="non_sensitive",
        stability="stable",
        current_request_safe=True,
    )["memory"]
    provider.prefetch_trace("baseline semantic production check")

    result = json.loads(provider.handle_tool_call("mnemosyne_memory", {
        "action": "semantic_recall",
        "query": "semantic full feature production",
    }))

    ids = [item["memory"]["id"] for item in result["results"]]
    assert safe["id"] in ids
    assert unsafe["id"] not in ids
