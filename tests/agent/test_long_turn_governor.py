import json
from types import SimpleNamespace

from agent.long_turn_governor import (
    LongTurnGovernor,
    LongTurnState,
    LongTurnThresholds,
    build_resume_packet,
    render_quiet_checkpoint,
)


def test_threshold_defaults_are_conservative():
    thresholds = LongTurnThresholds.from_mapping({})

    assert thresholds.api_calls == 30
    assert thresholds.tool_calls == 60
    assert thresholds.elapsed_seconds == 20 * 60
    assert thresholds.repeated_failure_warnings == 3


def test_api_threshold_persists_resume_packet_without_visible_spam(tmp_path):
    state = LongTurnState(session_id="sess/1", task_id="task-1", turn_id="turn-1")
    governor = LongTurnGovernor(
        state=state,
        thresholds=LongTurnThresholds(api_calls=2, tool_calls=60, elapsed_seconds=9999),
        persist_dir=tmp_path,
    )

    assert governor.mark_api_call(1)["threshold_reasons"] == []
    signal = governor.mark_api_call(2)

    assert signal["threshold_reasons"] == ["api_calls"]
    assert state.resume_packet_path is not None
    packet_path = tmp_path / "sess-1" / "turn-1.json"
    assert packet_path.exists()
    packet = json.loads(packet_path.read_text())
    assert packet["schema_version"] == 1
    assert packet["reason"] == "api_calls"
    assert packet["runtime_signal"]["api_calls"] == 2
    rendered = render_quiet_checkpoint(state)
    assert "api=2" in rendered
    assert "resume=" in rendered


def test_guardrail_repeated_failure_threshold_records_evidence_and_packet(tmp_path):
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    governor = LongTurnGovernor(
        state=state,
        thresholds=LongTurnThresholds(repeated_failure_warnings=2),
        persist_dir=tmp_path,
    )
    decision = SimpleNamespace(
        action="warn",
        code="repeated_exact_failure_warning",
        tool_name="terminal",
        count=2,
        to_metadata=lambda: {"code": "repeated_exact_failure_warning", "tool_name": "terminal"},
    )

    governor.observe_guardrail(decision)
    signal = governor.observe_guardrail(decision)

    assert signal["signal"] == "threshold_crossed"
    assert signal["threshold_reasons"] == ["repeated_failure_warnings"]
    assert state.guardrail_warnings == 2
    assert len(state.evidence_ledger) == 2
    assert state.resume_packet_path


def test_role_handoff_ledger_is_in_resume_packet(tmp_path):
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    governor = LongTurnGovernor(state=state, persist_dir=tmp_path)
    governor.add_evidence("verification", "Vex passed focused tests")
    governor.add_role_handoff(
        from_role="Forge",
        to_role="Vex",
        reason="independent verification",
        evidence_refs=[0],
    )

    packet = build_resume_packet(state, reason="manual")

    assert packet["role_handoffs"][0]["from_role"] == "Forge"
    assert packet["role_handoffs"][0]["to_role"] == "Vex"
    assert packet["evidence_ledger"][0]["summary"] == "Vex passed focused tests"


def test_resume_packet_persistence_failure_is_best_effort(tmp_path):
    persist_file = tmp_path / "not-a-directory"
    persist_file.write_text("x")
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    governor = LongTurnGovernor(
        state=state,
        thresholds=LongTurnThresholds(api_calls=1, tool_calls=999, elapsed_seconds=9999),
        persist_dir=persist_file,
    )

    signal = governor.mark_api_call(1)

    assert signal["threshold_reasons"] == ["api_calls"]
    assert signal["resume_packet_path"] is None
    assert signal["resume_packet_error"].startswith("FileExistsError:")
    assert state.last_signal == "resume_packet_error"


def test_repeated_same_tool_test_error_same_hypothesis_triggers_fallback_packet(tmp_path):
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    governor = LongTurnGovernor(state=state, persist_dir=tmp_path)
    args = {"test": "pytest tests/foo.py::test_bar", "hypothesis": "missing import"}
    result = "AssertionError: expected 1 got 2"

    assert governor.observe_tool_failure("terminal", args, result)["fallback_decision"] is None
    signal = governor.observe_tool_failure("terminal", args, result)

    decision = signal["fallback_decision"]
    assert decision["action"] == "pause"
    assert decision["reason"] == "repeated_failure_same_hypothesis"
    assert decision["count"] == 2
    assert state.resume_packet_path
    packet = json.loads(open(state.resume_packet_path).read())
    assert packet["reason"] == "repeated_failure_fallback"
    assert packet["fallback_decision"] == decision
    assert packet["closure_contract"]["fallback_decision"] == decision


def test_repeated_same_tool_test_error_changed_hypothesis_allows_one_bounded_retry(tmp_path):
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    governor = LongTurnGovernor(state=state, persist_dir=tmp_path)
    result = "AssertionError: expected 1 got 2"

    first = governor.observe_tool_failure(
        "terminal",
        {"test": "pytest tests/foo.py::test_bar", "hypothesis": "missing import"},
        result,
    )
    second = governor.observe_tool_failure(
        "terminal",
        {"test": "pytest tests/foo.py::test_bar", "hypothesis": "fixture order"},
        result,
    )

    assert first["fallback_decision"] is None
    assert second["fallback_decision"] is None
    assert second["changed_hypothesis_retry"]["used"] is True
    assert state.resume_packet_path is None

    third = governor.observe_tool_failure(
        "terminal",
        {"test": "pytest tests/foo.py::test_bar", "hypothesis": "fixture order"},
        result,
    )

    decision = third["fallback_decision"]
    assert decision["action"] == "pause"
    assert decision["reason"] == "repeated_failure_changed_hypothesis_exhausted"
    assert decision["count"] == 3
