from gateway.biff_synthetic_checks import FAIL, run_synthetic_checks, summarize_synthetic_checks


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
