import importlib.util
import json
import os
import sqlite3
import time
from pathlib import Path

from agent.biff_intent_router import plan_biff_turn
from gateway.continuation_artifacts import write_continuation_artifact
from gateway.session_hygiene import (
    biff_discord_quick_check_budget_prompt,
    resolve_biff_live_tool_guardrail_settings,
)
from tools.chat_guardrails import ChatToolPolicy, apply_chat_tool_policy, clear_chat_tool_policy, set_chat_tool_policy


def _load_script(path: str):
    spec = importlib.util.spec_from_file_location(Path(path).stem.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_discord_quick_check_budget_contract_keeps_broad_verification_with_biff_without_explicit_handoff():
    plan = plan_biff_turn("Please verify every K card and all related runtime systems are really done")

    assert plan.action == "route_bundle"
    assert plan.runtime == "workflow"
    assert plan.background is False
    assert plan.specialist is None
    assert plan.allow_bundle_selection is True


def test_discord_ordinary_workflow_guardrail_defaults_to_two_tools_and_prompt_contract():
    settings = resolve_biff_live_tool_guardrail_settings({}, "discord", message="is the gateway running?")

    assert settings["max_tool_calls"] <= 2
    prompt = biff_discord_quick_check_budget_prompt(settings)
    assert "1-2 narrow quick-check tool calls" in prompt
    assert "continuation handle" in prompt


def test_live_tool_budget_error_names_continuation_handle():
    task_id = "discord-budget-contract"
    set_chat_tool_policy(task_id, ChatToolPolicy(max_tool_calls=1))
    try:
        assert apply_chat_tool_policy("read_file", {"path": "x"}, task_id=task_id)[1] is None
        _args, error = apply_chat_tool_policy("search_files", {"pattern": "x"}, task_id=task_id)
        assert error is not None
        assert "Kanban" in error or "continuation" in error
    finally:
        clear_chat_tool_policy(task_id)


def test_continuation_artifact_redacts_and_writes_resume_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_CONTINUATION_DIR", str(tmp_path))

    artifact = write_continuation_artifact(
        user_request="fix it with sk-testSECRETSECRETSECRET",
        agent_result={"turn_exit_reason": "max_iterations_reached", "final_response": "checked A", "completed": False},
        session_id="sess/one",
        platform="discord",
        guardrail_settings={"max_tool_calls": 2},
        auto_continue=True,
        next_role="forge",
    )

    md_path = Path(artifact["artifact_paths"]["markdown"])
    json_path = Path(artifact["artifact_paths"]["json"])
    assert md_path.exists()
    assert json_path.exists()
    data = json.loads(json_path.read_text())
    assert data["auto_continue_started"] is True
    assert data["remaining_checks"]
    assert "SECRET" not in json_path.read_text()


def test_kanban_status_compact_direct_query_shapes_large_output(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "fixture")
    mod = _load_script("scripts/kanban-status-compact.py")
    from hermes_cli import kanban_db

    kanban_db.init_db(board="fixture")
    conn = kanban_db.connect(board="fixture")
    now = int(time.time())
    try:
        conn.execute(
            """
            INSERT INTO tasks (id, display_id, title, body, assignee, status, priority, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("t_fixture", "K-1331", "Fixture card", "body " * 500, "ranger", "blocked", 0, "test", now),
        )
        for idx in range(30):
            conn.execute(
                "INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?, ?, ?, ?)",
                ("t_fixture", "tester", f"blocking detail {idx} " + ("x" * 1000), now + idx),
            )
        conn.execute(
            "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
            ("t_fixture", "blocked", json.dumps({"reason": "fixture"}), now),
        )
        conn.commit()
    finally:
        conn.close()

    result = mod.compact_status(["K-1331"], board="fixture", limit=3)

    assert result["tasks"][0]["status"] == "blocked"
    assert "blocked" in result["tasks"][0]["attention"]
    assert len(result["tasks"][0]["recent_comments"]) == 3
    assert len(json.dumps(result)) < 6000


def test_secondbrain_verifier_pass_and_missing_schedule_warn(tmp_path):
    mod = _load_script("scripts/verify-secondbrain-smart-connections.py")
    vault = tmp_path / "SecondBrain"
    sc_dir = vault / ".obsidian" / "plugins" / "smart-connections"
    sc_dir.mkdir(parents=True)
    db = sc_dir / "smart-connections.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO embeddings (body) VALUES ('redacted')")
        conn.commit()
    finally:
        conn.close()
    schedule = tmp_path / "refresh.plist"
    schedule.write_text("scheduled")

    passing = mod.verify_secondbrain(vault=vault, smart_connections_dir=sc_dir, sqlite_db=db, schedule=schedule)
    warn = mod.verify_secondbrain(vault=vault, smart_connections_dir=sc_dir, sqlite_db=db, schedule=tmp_path / "missing.plist")

    assert passing["verdict"] == "PASS"
    assert passing["components"][2]["counts"]["embeddings"] == 1
    assert warn["verdict"] == "WARN"
    assert any("scheduled refresh" in item for item in warn["follow_up"])
