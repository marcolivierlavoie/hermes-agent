"""Hermes tool entrypoint for Biff household assist and safety signal MVPs."""
from __future__ import annotations

import json
from typing import Any

from biff_household.assist import build_assist_card, explain_automation_outcome
from biff_household.safety import build_safety_digest, load_signal_inventory, load_vision_edge_cases
from tools.registry import registry, tool_error


def biff_household(args: dict[str, Any], **kwargs) -> str:
    action = args.get("action")
    try:
        if action == "assist_card":
            out = build_assist_card(
                mode=args.get("mode") or "home",
                observation=args.get("observation"),
                evidence=args.get("evidence"),
                parked_reminder_state=args.get("parked_reminder_state"),
            )
        elif action == "automation_outcome":
            out = explain_automation_outcome(args.get("event") or {})
        elif action == "safety_digest":
            out = build_safety_digest(args.get("signals") or [], period=args.get("period") or "morning")
        elif action == "signal_inventory":
            out = load_signal_inventory()
        elif action == "vision_edge_cases":
            out = load_vision_edge_cases()
        else:
            return tool_error("Unknown action. Use assist_card, automation_outcome, safety_digest, signal_inventory, or vision_edge_cases.")
        return json.dumps(out, ensure_ascii=False)
    except Exception as exc:  # pragma: no cover - defensive tool boundary
        return tool_error(f"biff_household failed: {exc}")


BIFF_HOUSEHOLD_SCHEMA = {
    "name": "biff_household",
    "description": (
        "Biff household production MVP tool. Builds compact Household Assist state cards, "
        "short cooking/device/home troubleshooting flows, automation outcome explainers, "
        "low-noise morning/evening safety digests, safe signal inventory, and vision edge-case registry. "
        "Use existing subsystem snapshots (Home Assistant, n8n, vehicle/manual evidence); do not duplicate ledgers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["assist_card", "automation_outcome", "safety_digest", "signal_inventory", "vision_edge_cases"],
            },
            "mode": {"type": "string", "enum": ["cooking", "device", "home"], "description": "Assist mode for assist_card."},
            "observation": {"type": "string", "description": "Live user observation to include in the verified layer."},
            "evidence": {"type": "array", "items": {"type": "object"}, "description": "Evidence snapshots for assist_card."},
            "parked_reminder_state": {"type": "string", "description": "Optional parked/reminder state for assist_card."},
            "event": {"type": "object", "description": "Automation event for automation_outcome."},
            "signals": {"type": "array", "items": {"type": "object"}, "description": "Safety signal snapshots for safety_digest."},
            "period": {"type": "string", "enum": ["morning", "evening", "ad-hoc"], "description": "Digest period."},
        },
        "required": ["action"],
    },
}


registry.register(
    name="biff_household",
    toolset="biff",
    schema=BIFF_HOUSEHOLD_SCHEMA,
    handler=biff_household,
    check_fn=lambda: True,
    emoji="🏠",
)
