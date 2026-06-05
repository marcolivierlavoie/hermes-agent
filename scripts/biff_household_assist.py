#!/usr/bin/env python3
"""CLI entrypoint for BIF-650/BIF-654 household assist MVP."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biff_os.household_assist import (  # noqa: E402
    Evidence,
    build_safety_digest,
    build_state_card,
    default_safety_inventory,
    explain_automation_outcome,
    render_digest,
    VISION_EDGE_CASE_REGISTRY,
)


def _load_json(path: str | None):
    if not path or path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text()
    return json.loads(text) if text.strip() else {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Biff household assist/safety MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    sc = sub.add_parser("state-card", help="Render a compact Household Assist state card")
    sc.add_argument("request")
    sc.add_argument("--mode", default="general", choices=["cooking", "device", "home", "general"])
    sc.add_argument("--evidence", action="append", default=[], help="label=value evidence; repeatable")
    sc.add_argument("--json", action="store_true")

    ao = sub.add_parser("automation-outcome", help="Explain whether an automation ran and why")
    ao.add_argument("--input", "-i", help="JSON file/stdin with name, ran, gates, notification_sent, evidence, freshness")
    ao.add_argument("--json", action="store_true")

    inv = sub.add_parser("safety-inventory", help="List safe household safety signals")
    inv.add_argument("--json", action="store_true")

    dig = sub.add_parser("safety-digest", help="Render low-noise morning/evening safety digest")
    dig.add_argument("--period", default="ad-hoc", choices=["morning", "evening", "ad-hoc"])
    dig.add_argument("--input", "-i", help="JSON signal list; defaults to inventory")
    dig.add_argument("--json", action="store_true")

    reg = sub.add_parser("vision-registry", help="Print household vision edge-case registry")
    reg.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "state-card":
        evidence = []
        for item in args.evidence:
            label, _, value = item.partition("=")
            evidence.append(Evidence(label=label.strip(), value=value.strip(), source="cli", freshness="live-user"))
        card = build_state_card(args.request, mode=args.mode, evidence=evidence)
        print(json.dumps(card.to_dict(), indent=2) if args.json else card.render())
        return 0

    if args.command == "automation-outcome":
        data = _load_json(args.input)
        outcome = explain_automation_outcome(
            name=data.get("name", "automation"),
            ran=bool(data.get("ran", False)),
            gates=data.get("gates", ()),
            notification_sent=bool(data.get("notification_sent", False)),
            evidence=data.get("evidence", ()),
            freshness=data.get("freshness", "unknown"),
        )
        print(json.dumps(outcome.to_dict(), indent=2) if args.json else outcome.render())
        return 0

    if args.command == "safety-inventory":
        data = [s.to_dict() for s in default_safety_inventory()]
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            for signal in default_safety_inventory():
                print(f"- {signal.name}: source={signal.source}; surface={signal.surface_when}; suppress={signal.suppress_when}")
        return 0

    if args.command == "safety-digest":
        signals = _load_json(args.input) if args.input else [s.to_dict() for s in default_safety_inventory()]
        digest = build_safety_digest(signals, period=args.period)
        print(json.dumps(digest, indent=2) if args.json else render_digest(digest))
        return 0

    if args.command == "vision-registry":
        data = list(VISION_EDGE_CASE_REGISTRY)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            for item in data:
                print(f"- {item['classifier']}: positive={', '.join(item['positive'])}; negative={', '.join(item['negative'])}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
