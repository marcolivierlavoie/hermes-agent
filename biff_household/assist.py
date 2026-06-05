from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Mapping


ASSIST_CARD_VERSION = "household-assist-card/v1"


@dataclass(frozen=True)
class EvidenceItem:
    source: str
    observed_at: str | None = None
    freshness: str = "unknown"
    summary: str = ""


@dataclass(frozen=True)
class AssistCard:
    card_type: str = ASSIST_CARD_VERSION
    current_state: str = "Unknown"
    verified_layer: list[EvidenceItem] = field(default_factory=list)
    next_action: str = "Ask for one concrete observation."
    stop_condition: str = "Stop if safety risk, uncertainty, or the user asks to pause."
    parked_reminder_state: str | None = None
    confidence: str = "low"
    mode: str = "household_assist"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verified_layer"] = [asdict(item) for item in self.verified_layer]
        return data


FLOW_LIBRARY: dict[str, dict[str, Any]] = {
    "cooking": {
        "state": "Cooking assist active: keep instructions short and confirm doneness/safety before advancing.",
        "key_check": "What dish/step are we on, and is heat/knife/raw-meat risk present?",
        "next_action": "Give exactly one next cooking step, including a timer or visual cue if relevant.",
        "stop_condition": "Stop if smoke, burning smell, food safety doubt, allergy concern, or Marco asks for full recipe mode.",
        "confidence": "medium",
    },
    "device": {
        "state": "Device troubleshooting active: isolate power/network/app state before changing settings.",
        "key_check": "Is it powered, connected, and showing an error/state indicator?",
        "next_action": "Ask for or verify one observable state, then suggest the lowest-risk reversible fix.",
        "stop_condition": "Stop before factory reset, credential change, purchase, or destructive setting change.",
        "confidence": "medium",
    },
    "home": {
        "state": "Home troubleshooting active: prioritize physical safety and reversible checks.",
        "key_check": "Is there water, heat, electrical, lock, child, pet, or weather exposure risk?",
        "next_action": "Ask for one quick physical check or sensor reading, then give one reversible next action.",
        "stop_condition": "Stop and escalate for fire, gas, electrical hazard, leak spread, security risk, or injury risk.",
        "confidence": "medium",
    },
}


def _freshness(observed_at: str | None, now: datetime | None = None) -> str:
    if not observed_at:
        return "unknown"
    now = now or datetime.now(timezone.utc)
    try:
        ts = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        return "invalid timestamp"
    delta = now - ts.astimezone(timezone.utc)
    minutes = int(delta.total_seconds() // 60)
    if minutes < 0:
        return "future timestamp"
    if minutes <= 15:
        return f"fresh ({minutes}m old)"
    if minutes <= 180:
        return f"recent ({minutes}m old)"
    return f"stale ({minutes}m old)"


def build_assist_card(
    mode: str,
    observation: str | None = None,
    evidence: list[Mapping[str, Any]] | None = None,
    parked_reminder_state: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the compact Household Assist state card contract.

    mode accepts cooking, device, home, or a custom value. The card stays compact:
    current state, verified layer, next action, stop condition, parked/reminder state,
    and confidence.
    """
    spec = FLOW_LIBRARY.get(mode, FLOW_LIBRARY["home"])
    verified: list[EvidenceItem] = []
    if observation:
        verified.append(EvidenceItem(source="user_observation", freshness="live", summary=observation))
    for item in evidence or []:
        observed_at = item.get("observed_at") or item.get("last_changed")
        verified.append(
            EvidenceItem(
                source=str(item.get("source") or item.get("entity_id") or "unknown"),
                observed_at=observed_at,
                freshness=str(item.get("freshness") or _freshness(observed_at, now)),
                summary=str(item.get("summary") or item.get("state") or ""),
            )
        )
    if not verified:
        verified.append(EvidenceItem(source="none", freshness="none", summary=spec["key_check"]))
    card = AssistCard(
        current_state=spec["state"],
        verified_layer=verified,
        next_action=spec["next_action"],
        stop_condition=spec["stop_condition"],
        parked_reminder_state=parked_reminder_state,
        confidence=spec["confidence"] if observation or evidence else "low",
    )
    data = card.to_dict()
    data["mode"] = mode
    data["key_check"] = spec["key_check"]
    return data


def explain_automation_outcome(event: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Normalize an automation run/not-run outcome with gates, notification, evidence, and freshness."""
    ran = bool(event.get("ran"))
    gates_in = event.get("gates") or []
    gates = []
    failed = []
    for gate in gates_in:
        passed = bool(gate.get("passed"))
        row = {
            "name": str(gate.get("name") or "unnamed_gate"),
            "passed": passed,
            "reason": str(gate.get("reason") or ("passed" if passed else "failed")),
        }
        gates.append(row)
        if not passed:
            failed.append(row)
    observed_at = event.get("observed_at") or event.get("last_run_at")
    return {
        "automation": str(event.get("automation") or event.get("name") or "unknown"),
        "outcome": "ran" if ran else "not-ran",
        "gate_summary": "passed" if gates and not failed else ("failed" if failed else "not provided"),
        "gates": gates,
        "notification_sent": bool(event.get("notification_sent")),
        "notification_target": event.get("notification_target"),
        "evidence": event.get("evidence") or [],
        "observed_at": observed_at,
        "freshness": str(event.get("freshness") or _freshness(observed_at, now)),
        "explanation": _automation_sentence(ran, failed, bool(event.get("notification_sent"))),
    }


def _automation_sentence(ran: bool, failed: list[Mapping[str, Any]], notified: bool) -> str:
    if ran:
        base = "Automation ran."
    elif failed:
        base = "Automation did not run because gate failed: " + ", ".join(g["name"] for g in failed) + "."
    else:
        base = "Automation did not run; no failing gate was supplied."
    return base + (" Notification was sent." if notified else " No notification was sent.")
