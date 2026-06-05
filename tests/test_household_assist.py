from biff_os.household_assist import (
    Evidence,
    SafetySignal,
    build_safety_digest,
    build_state_card,
    explain_automation_outcome,
    render_digest,
    VISION_EDGE_CASE_REGISTRY,
)


def test_state_card_contract_has_required_fields():
    card = build_state_card("SmokeFire temp is dropping", mode="cooking", evidence=[Evidence("temp", "180F", "HA", freshness="2m")])
    data = card.to_dict()
    assert set(data) == {"current_state", "verified_layer", "next_action", "stop_condition", "parked_reminder_state", "confidence", "evidence"}
    assert data["confidence"] == "high"
    assert "timer" in data["next_action"] or "temp" in data["next_action"]


def test_automation_outcome_explains_failed_gate():
    outcome = explain_automation_outcome(
        "patio rain reminder",
        ran=False,
        gates=[{"name": "forecast", "passed": False, "reason": "rain probability below threshold"}],
        notification_sent=False,
        evidence=[{"label": "rain", "value": "10%", "source": "weather", "freshness": "fresh"}],
        freshness="fresh",
    )
    assert not outcome.ran
    assert "Blocked by forecast" in outcome.summary
    assert "no notification sent" in outcome.render()


def test_safety_digest_suppresses_normal_noise_and_surfaces_action():
    digest = build_safety_digest([
        SafetySignal("Front door", "HA", "home", "private", "<=15m", "open", "closed", current_value="closed", severity="info"),
        SafetySignal("Garage", "HA", "home", "private", "<=15m", "open overnight", "closed", current_value="open for 45m", severity="action"),
    ], period="evening")
    assert digest["status"] == "attention"
    assert len(digest["signals"]) == 1
    assert digest["signals"][0]["name"] == "Garage"
    assert digest["suppressed_count"] == 1
    assert "Garage" in render_digest(digest)


def test_vision_registry_contains_patio_exclusions():
    patio = [item for item in VISION_EDGE_CASE_REGISTRY if item["classifier"] == "patio_items_exposed_to_rain"][0]
    assert "orange parasol" in patio["negative"]
    assert "normal BBQ tarp" in patio["negative"]
