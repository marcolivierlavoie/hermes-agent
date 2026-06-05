from gateway.biff_responsiveness_qa import (
    FAIL,
    PASS,
    PENDING,
    QA_CHECKS,
    run_responsiveness_qa,
    summarize_responsiveness_qa,
)


def test_responsiveness_qa_matrix_covers_full_roadmap():
    assert [check.card for check in QA_CHECKS] == [f"K-{n}" for n in range(1291, 1304)] + ["K-1308"]
    assert all(check.real_life_usage for check in QA_CHECKS)


def test_responsiveness_qa_passes_delivered_live_chat_checks():
    delivered_cards = [
        "K-1291",
        "K-1292",
        "K-1293",
        "K-1294",
        "K-1295",
        "K-1296",
        "K-1297",
        "K-1298",
        "K-1299",
        "K-1300",
        "K-1301",
        "K-1302",
        "K-1303",
        "K-1308",
    ]

    results = run_responsiveness_qa(delivered_cards)
    assert {result.status for result in results} == {PASS}


def test_responsiveness_qa_has_no_pending_items_after_roadmap_completion():
    pending_cards = []

    results = run_responsiveness_qa(pending_cards)
    assert {result.status for result in results} == {PASS}
    summary = summarize_responsiveness_qa(results)
    assert summary[PASS] == len(results)
    assert summary[FAIL] == 0
    assert summary[PENDING] == 0
