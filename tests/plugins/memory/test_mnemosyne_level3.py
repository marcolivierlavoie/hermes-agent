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
    risky = provider.prefetch_trace("Tell me Marco token or password for Biff OS")
    event_rows = (tmp_path / "mnemosyne" / "isolated-pilot" / "events.jsonl").read_text(encoding="utf-8").splitlines()

    assert trace["injected"] is True
    assert trace["budgets"]["max_results"] == 2
    assert good["id"] in trace["memory_ids"]
    assert any(item["skip_reason"] == "confidence_not_high" for item in trace["candidate_traces"])
    assert risky["injected"] is False
    assert risky["skip_reason"] == "blocked_or_risky_query"
    assert event_rows
    assert "context" not in json.loads(event_rows[-1])


def test_source_aware_seeding_is_capped_candidate_only_and_dry_run_safe(tmp_path):
    provider = _provider(tmp_path)

    dry_run = provider.seed_source_candidates(
        source="BIF-577 seed fixture",
        records=[{"content": "Seeded fact candidate for Biff OS.", "context": "seed test"}],
        dry_run=True,
    )
    assert dry_run["success"] is True
    assert dry_run["dry_run"] is True
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
    assert len(provider.list_candidates()) == 1
    assert provider.recall("Second seeded fact candidate") == []
    assert bulk["success"] is False
    assert "Refusing bulk seed" in bulk["error"]
