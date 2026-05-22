from datetime import datetime, timezone

from biff_household.assist import build_assist_card, explain_automation_outcome
from biff_household.safety import build_safety_digest, load_signal_inventory, load_vision_edge_cases

NOW = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)


def test_assist_card_contract_for_cooking():
    card = build_assist_card("cooking", "pasta water is boiling", now=NOW)
    assert card["card_type"] == "household-assist-card/v1"
    assert card["mode"] == "cooking"
    assert card["current_state"]
    assert card["verified_layer"][0]["source"] == "user_observation"
    assert card["next_action"]
    assert card["stop_condition"]
    assert "confidence" in card
    assert "key_check" in card


def test_automation_outcome_explains_failed_gate_and_notification():
    out = explain_automation_outcome(
        {
            "automation": "patio rain warning",
            "ran": False,
            "notification_sent": False,
            "observed_at": "2026-05-21T11:30:00Z",
            "gates": [
                {"name": "rain_risk", "passed": True},
                {"name": "fresh_vision", "passed": False, "reason": "camera evidence stale"},
            ],
            "evidence": [{"source": "ha.weather", "summary": "rain likely"}],
        },
        now=NOW,
    )
    assert out["outcome"] == "not-ran"
    assert out["gate_summary"] == "failed"
    assert "fresh_vision" in out["explanation"]
    assert out["freshness"] == "recent (30m old)"


def test_safety_digest_surfaces_warning_and_suppresses_nominal():
    digest = build_safety_digest(
        [
            {
                "id": "car_sunroof_windows",
                "title": "Car sunroof",
                "status": "open",
                "severity": "warning",
                "observed_at": "2026-05-21T11:55:00Z",
                "evidence": [{"source": "car snapshot", "summary": "sunroof open"}],
            },
            {
                "id": "ha_doors_windows",
                "title": "Back door",
                "status": "closed",
                "severity": "info",
                "observed_at": "2026-05-21T11:59:00Z",
            },
        ],
        period="evening",
        now=NOW,
    )
    assert digest["digest_type"] == "household-safety-digest/v1"
    assert digest["period"] == "evening"
    assert len(digest["items"]) == 1
    assert digest["items"][0]["id"] == "car_sunroof_windows"
    assert digest["suppressed_count"] == 1


def test_inventory_and_edge_case_registry_load():
    inventory = load_signal_inventory()
    edge_cases = load_vision_edge_cases()
    assert {s["id"] for s in inventory["signals"]} >= {
        "ha_doors_windows",
        "car_sunroof_windows",
        "critical_automation_outcomes",
    }
    assert any("orange parasol" in " ".join(case["exclusions"]) for case in edge_cases["edge_cases"])
