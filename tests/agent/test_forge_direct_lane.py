from agent.forge_direct_lane import FORGE_DIRECT_BUNDLE_KEY, build_forge_direct_instruction
from agent.quill_direct_lane import build_quill_direct_instruction
from agent.ranger_direct_lane import build_ranger_direct_instruction
from agent.vex_direct_lane import build_vex_direct_instruction


def test_forge_direct_instruction_preserves_user_request_and_contract():
    message = build_forge_direct_instruction("Fix the gateway restart loop and add tests.")

    assert FORGE_DIRECT_BUNDLE_KEY == "/biff-hermes-runtime-change"
    assert "Fix the gateway restart loop and add tests." in message
    assert "Delegate to Forge immediately" in message
    assert "Use Kanban as the lightweight ledger only" in message
    assert "preferring rg and bounded paths" in message
    assert "Run focused tests" in message
    assert "rebuild the web bundle" in message
    assert "verify the live UI" in message
    assert "Do not report the task as done until build/restart/live verification steps" in message
    assert "Restart the Hermes gateway only when runtime changes need it" in message
    assert "Use Vex, Quill, Ranger, or other specialists only when the task actually needs their role" in message
    assert "Do not call kanban_show with no task_id" in message


def test_direct_lane_prompts_warn_against_worker_only_kanban_show():
    prompts = [
        build_quill_direct_instruction("Research this Reddit thread."),
        build_ranger_direct_instruction("Clean up the board."),
        build_vex_direct_instruction("QA this behavior."),
    ]

    for message in prompts:
        assert "kanban_show" in message
        assert "task_id" in message
