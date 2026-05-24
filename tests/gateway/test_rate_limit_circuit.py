from pathlib import Path

from gateway import rate_limit_circuit as circuit


def test_rate_limit_circuit_detects_usage_limit_payload():
    assert circuit.looks_like_provider_rate_limit(
        {"error": {"type": "usage_limit_reached", "message": "The usage limit has been reached"}}
    )
    assert circuit.looks_like_provider_rate_limit("Error code: 429 - Rate limit exceeded")
    assert not circuit.looks_like_provider_rate_limit("regular auth setup reminder")


def test_result_rate_limit_detection_ignores_normal_answer_text():
    assert not circuit.result_has_provider_rate_limit(
        {
            "error": None,
            "final_response": "Here is how to set up quotas, backups, and retry behavior.",
            "turn_exit_reason": None,
        }
    )


def test_result_rate_limit_detection_reads_error_fields():
    assert circuit.result_has_provider_rate_limit(
        {
            "error": "Error code: 429 - Rate limit exceeded",
            "final_response": "I could not finish.",
        }
    )


def test_rate_limit_circuit_records_expires_and_clears(monkeypatch, tmp_path):
    monkeypatch.setattr(circuit, "get_hermes_home", lambda: Path(tmp_path))

    circuit.record_rate_limit(
        scope="discord",
        reason="429",
        cooldown_seconds=60,
        now=1000,
    )

    active = circuit.active_rate_limit(scope="discord", now=1010)
    assert active is not None
    assert active["remaining_seconds"] == 50

    assert circuit.active_rate_limit(scope="discord", now=1061) is None
