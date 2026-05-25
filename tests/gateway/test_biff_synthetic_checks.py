from gateway.biff_synthetic_checks import (
    FAIL,
    run_runtime_guardrail_smoke_checks,
    run_synthetic_checks,
    summarize_synthetic_checks,
)


def test_first_three_synthetic_checks_pass():
    results = run_synthetic_checks(
        [
            "casual_answer_chicken_rice",
            "read_only_kanban_status",
            "keep_story_open",
        ]
    )

    assert summarize_synthetic_checks(results) == {"pass": 3, "fail": 0}


def test_full_planner_synthetic_suite_passes():
    results = run_synthetic_checks()

    assert summarize_synthetic_checks(results).get(FAIL, 0) == 0


def test_runtime_guardrail_synthetic_smoke_suite_passes():
    results = run_runtime_guardrail_smoke_checks()

    assert summarize_synthetic_checks(results).get(FAIL, 0) == 0
    assert {result.name for result in results} == {
        "current_chat_replies",
        "busy_message_queueing",
        "steer_interruption",
        "nonblocking_specialist_dispatch",
        "completion_closeout_delivery",
        "vex_closeout_pass_block_gate",
        "session_rollover_resume",
        "loop_bounding_no_empty_search_loops",
        "update_notifications_noop_degrade",
    }
