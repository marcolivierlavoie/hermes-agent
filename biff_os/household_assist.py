"""Household Assist Mode and Safety Signal Center MVP.

This module is intentionally dependency-light so it can run inside the live
Hermes/Biff runtime, cron/n8n shell steps, and tests without extra services.
It provides:
- a compact Household Assist state-card contract,
- short cooking/device/home troubleshooting flows,
- automation outcome explainers with evidence/freshness,
- a low-noise safety signal inventory + digest, and
- a household vision edge-case registry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Literal

Confidence = Literal["low", "medium", "high"]
Severity = Literal["info", "watch", "action", "critical"]


class AssistMode(str, Enum):
    COOKING = "cooking"
    DEVICE = "device"
    HOME = "home"
    GENERAL = "general"


@dataclass(frozen=True)
class Evidence:
    label: str
    value: str
    source: str = "user"
    observed_at: str | None = None
    freshness: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StateCard:
    current_state: str
    verified_layer: str
    next_action: str
    stop_condition: str
    parked_reminder_state: str | None = None
    confidence: Confidence = "medium"
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [item.to_dict() for item in self.evidence]
        return data

    def render(self) -> str:
        evidence = "; ".join(f"{e.label}={e.value} ({e.source}, {e.freshness})" for e in self.evidence) or "none yet"
        parked = f"\nParked/reminder: {self.parked_reminder_state}" if self.parked_reminder_state else ""
        return (
            f"State: {self.current_state}\n"
            f"Verified: {self.verified_layer}\n"
            f"Next: {self.next_action}\n"
            f"Stop when: {self.stop_condition}\n"
            f"Confidence: {self.confidence}\n"
            f"Evidence: {evidence}"
            f"{parked}"
        )


_ASSIST_TEMPLATES: dict[AssistMode, dict[str, str]] = {
    AssistMode.COOKING: {
        "verified": "recipe step, heat/source state, and any timing/temperature evidence available",
        "next": "do the single next cooking action; include a timer/temp check if it prevents overcooking",
        "stop": "food is safe/ready, heat is off, or Marco says to switch into full recipe mode",
    },
    AssistMode.DEVICE: {
        "verified": "device identity, power/network state, and last user-observed symptom",
        "next": "perform the safest reversible check first; avoid resets or account changes unless explicitly requested",
        "stop": "device works, the next step is destructive/security-sensitive, or the symptom changes",
    },
    AssistMode.HOME: {
        "verified": "physical location, relevant sensor/home state, and what has already been checked",
        "next": "make the shortest physical check or low-risk adjustment",
        "stop": "risk is cleared, a human inspection is needed, or confidence drops below medium",
    },
    AssistMode.GENERAL: {
        "verified": "current request and any available evidence",
        "next": "take one low-risk concrete next step",
        "stop": "the task is done, risk increases, or Marco asks for a deeper investigation",
    },
}


def build_state_card(
    request: str,
    mode: str = "general",
    evidence: Iterable[Evidence | dict[str, Any]] = (),
    parked_reminder_state: str | None = None,
) -> StateCard:
    """Create the compact household-assist state card for a live moment."""
    assist_mode = AssistMode(mode) if mode in AssistMode._value2member_map_ else AssistMode.GENERAL
    template = _ASSIST_TEMPLATES[assist_mode]
    ev = tuple(item if isinstance(item, Evidence) else Evidence(**item) for item in evidence)
    confidence: Confidence = "high" if ev else "medium"
    return StateCard(
        current_state=request.strip() or "Household assist request is active.",
        verified_layer=template["verified"],
        next_action=template["next"],
        stop_condition=template["stop"],
        parked_reminder_state=parked_reminder_state,
        confidence=confidence,
        evidence=ev,
    )


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AutomationOutcome:
    name: str
    ran: bool
    gates: tuple[GateResult, ...]
    notification_sent: bool
    evidence: tuple[Evidence, ...]
    freshness: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["gates"] = [gate.to_dict() for gate in self.gates]
        data["evidence"] = [item.to_dict() for item in self.evidence]
        return data

    def render(self) -> str:
        ran = "ran" if self.ran else "did not run"
        gates = "; ".join(f"{g.name}: {'passed' if g.passed else 'failed'} — {g.reason}" for g in self.gates) or "no gates recorded"
        notif = "notification sent" if self.notification_sent else "no notification sent"
        evidence = "; ".join(f"{e.label}={e.value} ({e.source}, {e.freshness})" for e in self.evidence) or "none"
        return f"{self.name}: {ran}. {self.summary}\nGates: {gates}\nNotification: {notif}\nEvidence: {evidence}\nFreshness: {self.freshness}"


def explain_automation_outcome(
    name: str,
    ran: bool,
    gates: Iterable[GateResult | dict[str, Any]],
    notification_sent: bool,
    evidence: Iterable[Evidence | dict[str, Any]],
    freshness: str = "unknown",
) -> AutomationOutcome:
    gate_tuple = tuple(g if isinstance(g, GateResult) else GateResult(**g) for g in gates)
    evidence_tuple = tuple(e if isinstance(e, Evidence) else Evidence(**e) for e in evidence)
    failed = [g for g in gate_tuple if not g.passed]
    if ran:
        summary = "All required gates passed before execution."
    elif failed:
        summary = f"Blocked by {failed[0].name}: {failed[0].reason}"
    else:
        summary = "No failed gate was recorded; treat as unknown until automation logs are inspected."
    return AutomationOutcome(name, ran, gate_tuple, notification_sent, evidence_tuple, freshness, summary)


@dataclass(frozen=True)
class SafetySignal:
    name: str
    source: str
    category: str
    sensitivity: str
    freshness_expectation: str
    surface_when: str
    suppress_when: str
    current_value: str | None = None
    severity: Severity = "info"
    last_seen: str | None = None
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [item.to_dict() for item in self.evidence]
        return data


def default_safety_inventory() -> tuple[SafetySignal, ...]:
    """Safe, low-noise household signals worth surfacing."""
    return (
        SafetySignal("Exterior doors/windows", "Home Assistant", "home", "household-private", "<=15m", "open unexpectedly, open overnight, or open during bad weather", "closed or intentionally open", severity="watch"),
        SafetySignal("Garage/open access", "Home Assistant", "home", "household-private", "<=15m", "left open, unexpected motion/open state", "closed and no recent anomaly", severity="action"),
        SafetySignal("Smoke/CO/water leak", "Home Assistant", "safety", "household-private", "immediate", "any alarm, unavailable critical sensor", "normal/available", severity="critical"),
        SafetySignal("Patio-cover rain risk", "Weather + household rules", "weather", "non-sensitive", "<=2h forecast", "rain risk while vulnerable outdoor items may be exposed", "dry forecast or outdoor state intentionally ignored", severity="watch"),
        SafetySignal("Car fuel/service/doors/sunroof", "Vehicle integration/manual note", "car", "location-adjacent", "<=12h", "low fuel/service due/open access before commute or overnight", "fresh normal state or no vehicle integration", severity="watch"),
        SafetySignal("Critical automations", "n8n/Home Assistant", "automation", "household-private", "last run or error <=24h", "missed expected run, repeated error, or notification failure", "healthy recent run", severity="action"),
        SafetySignal("HA/n8n availability", "service health", "platform", "operational", "<=15m", "service unreachable, stale data, or credential failure", "healthy", severity="action"),
    )


def actionable_signal(signal: SafetySignal) -> bool:
    # Inventory defaults describe what *would* be worth surfacing; they are not
    # active alerts until a fresh/current value indicates attention is needed.
    value = (signal.current_value or "").lower()
    if not value:
        return False
    return any(token in value for token in ("open", "alarm", "leak", "rain", "low", "due", "error", "failed", "stale", "unavailable"))


def build_safety_digest(
    signals: Iterable[SafetySignal | dict[str, Any]],
    period: Literal["morning", "evening", "ad-hoc"] = "ad-hoc",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a low-noise digest: critical/action first, suppress normal noise."""
    now = now or datetime.now(timezone.utc)
    normalized = []
    for item in signals:
        if isinstance(item, SafetySignal):
            normalized.append(item)
        else:
            ev = tuple(Evidence(**e) if isinstance(e, dict) else e for e in item.get("evidence", ()))
            normalized.append(SafetySignal(**{**item, "evidence": ev}))
    actionable = [s for s in normalized if actionable_signal(s)]
    severity_rank = {"critical": 0, "action": 1, "watch": 2, "info": 3}
    actionable.sort(key=lambda s: (severity_rank[s.severity], s.category, s.name))
    return {
        "period": period,
        "generated_at": now.isoformat(),
        "status": "clear" if not actionable else "attention",
        "summary": "No high-value household safety signals need attention." if not actionable else f"{len(actionable)} household safety signal(s) need attention.",
        "signals": [s.to_dict() for s in actionable],
        "suppressed_count": len(normalized) - len(actionable),
        "noise_policy": "Only critical/action states, stale safety evidence, or high-value weather/car/home risks surface; normal states are suppressed.",
    }


VISION_EDGE_CASE_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "classifier": "patio_items_exposed_to_rain",
        "positive": ["uncovered couch cushions", "blankets/towels left outside", "electronics/tools on patio"],
        "negative": ["orange parasol", "covered BBQ", "normal BBQ tarp", "stacked outdoor furniture under cover"],
        "needs_human_when": ["night/low-light image", "object partly occluded", "confidence below medium"],
        "automation_boundary": "Use as a reminder/evidence signal only; do not trigger destructive actions.",
    },
    {
        "classifier": "door_or_window_left_open",
        "positive": ["open exterior door/window sensor", "garage open sensor"],
        "negative": ["interior doors", "intentionally open supervised patio door"],
        "needs_human_when": ["sensor stale", "state conflicts with recent user statement"],
        "automation_boundary": "Notify/explain; never infer occupancy-sensitive details in broad summaries.",
    },
    {
        "classifier": "cooking_or_heat_risk",
        "positive": ["active timer overdue", "stove/oven/smoker mentioned as on", "smoke alarm"],
        "negative": ["recipe prep without heat", "historical cooking note"],
        "needs_human_when": ["no fresh heat evidence", "alarm present"],
        "automation_boundary": "Prioritize short next action and stop condition; escalate alarms immediately.",
    },
)


def render_digest(digest: dict[str, Any]) -> str:
    if digest["status"] == "clear":
        return f"{digest['period'].title()} safety digest: clear. {digest['summary']} Suppressed normal signals: {digest['suppressed_count']}."
    lines = [f"{digest['period'].title()} safety digest: {digest['summary']}"]
    for signal in digest["signals"]:
        value = f" — {signal['current_value']}" if signal.get("current_value") else ""
        lines.append(f"- [{signal['severity']}] {signal['name']}{value}; source={signal['source']}; freshness={signal['freshness_expectation']}")
    lines.append(f"Suppressed normal signals: {digest['suppressed_count']}. Policy: {digest['noise_policy']}")
    return "\n".join(lines)
