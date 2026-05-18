import json

from agent.long_turn_governor import (
    LongTurnGovernor,
    LongTurnState,
    build_resume_packet,
    build_turn_closure_contract,
    load_latest_resume_packet,
    render_action_relevant_checkpoint,
    render_resume_reanchor,
    should_render_action_relevant_checkpoint,
)


def test_resume_packet_includes_closure_contract_for_resumability(tmp_path):
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    governor = LongTurnGovernor(state=state, persist_dir=tmp_path)
    governor.add_checkpoint("after tests", {"next_action": "run focused gateway test"})
    governor.persist_resume_packet("interrupted_by_user")

    packet = json.loads((tmp_path / "sess" / "turn.json").read_text())

    assert packet["closure_contract"]["status"] == "resume_required"
    assert packet["closure_contract"]["resume_packet_path"].endswith("turn.json")
    assert packet["closure_contract"]["next_action"] == "run focused gateway test"
    assert packet["closure_contract"]["user_visible_claim"] == "not_complete"


def test_turn_closure_contract_marks_clean_completion_without_resume():
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    contract = build_turn_closure_contract(state, exit_reason="final_response")

    assert contract["status"] == "closed"
    assert contract["resume_required"] is False
    assert contract["user_visible_claim"] == "complete"


def test_turn_closure_contract_treats_run_agent_text_response_as_closed():
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    contract = build_turn_closure_contract(state, exit_reason="text_response(finish_reason=stop)")

    assert contract["status"] == "closed"
    assert contract["resume_required"] is False
    assert contract["user_visible_claim"] == "complete"


def test_gateway_checkpoint_rendering_is_action_relevant_and_path_safe(tmp_path):
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    governor = LongTurnGovernor(state=state, persist_dir=tmp_path)
    governor.add_checkpoint("verification", {"next_action": "ask Vex to review"})
    signal = governor.mark_api_call(30)

    rendered = render_action_relevant_checkpoint(signal, state)

    assert "Checkpoint saved" in rendered
    assert "api=30" in rendered
    assert "next: ask Vex to review" in rendered
    assert str(tmp_path) not in rendered


def test_threshold_only_long_turn_does_not_render_gateway_checkpoint_spam(tmp_path):
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    governor = LongTurnGovernor(state=state, persist_dir=tmp_path)
    signal = governor.mark_api_call(30)

    assert should_render_action_relevant_checkpoint(signal, turn_exit_reason="final_response") is False


def test_interrupted_resume_packet_can_be_selected_and_rendered_for_later_reanchor(tmp_path):
    old_state = LongTurnState(session_id="sess", task_id="task", turn_id="old")
    old_governor = LongTurnGovernor(state=old_state, persist_dir=tmp_path)
    old_governor.add_checkpoint("old", {"next_action": "ignore older packet"})
    old_governor.persist_resume_packet("interrupted_by_user")

    new_state = LongTurnState(session_id="sess", task_id="task", turn_id="new")
    new_governor = LongTurnGovernor(state=new_state, persist_dir=tmp_path)
    new_governor.add_checkpoint("verify", {"next_action": "run Vex verification"})
    new_governor.add_evidence("test", "focused tests were green")
    new_governor.persist_resume_packet("interrupted_by_user")

    packet = load_latest_resume_packet(tmp_path, "sess")
    rendered = render_resume_reanchor(packet)

    assert packet["closure_contract"]["turn_id"] == "new"
    assert "Resume context" in rendered
    assert "run Vex verification" in rendered
    assert "focused tests were green" in rendered
    assert str(tmp_path) not in rendered


def test_closed_packet_prevents_stale_resume_reanchor(tmp_path):
    state = LongTurnState(session_id="sess", task_id="task", turn_id="turn")
    governor = LongTurnGovernor(state=state, persist_dir=tmp_path)
    governor.mark_api_call(30)
    assert load_latest_resume_packet(tmp_path, "sess") is not None

    governor.persist_closure_packet("text_response(finish_reason=stop)")

    assert load_latest_resume_packet(tmp_path, "sess") is None
