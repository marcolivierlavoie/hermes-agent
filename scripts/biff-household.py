#!/usr/bin/env python3
"""CLI smoke/ops entrypoint for Biff household assist + safety MVP."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from biff_household.assist import build_assist_card, explain_automation_outcome
from biff_household.safety import build_safety_digest, load_signal_inventory, load_vision_edge_cases


def _load_json(path: str | None):
    if not path or path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text(encoding="utf-8")
    return json.loads(text) if text.strip() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    assist = sub.add_parser("assist-card")
    assist.add_argument("--mode", choices=["cooking", "device", "home"], default="home")
    assist.add_argument("--observation")
    assist.add_argument("--evidence-json", help="JSON file or '-' containing evidence array")
    assist.add_argument("--parked-reminder-state")

    outcome = sub.add_parser("automation-outcome")
    outcome.add_argument("event_json", help="JSON file or '-' containing automation event")

    digest = sub.add_parser("safety-digest")
    digest.add_argument("signals_json", help="JSON file or '-' containing signal snapshots array")
    digest.add_argument("--period", choices=["morning", "evening", "ad-hoc"], default="morning")

    sub.add_parser("signal-inventory")
    sub.add_parser("vision-edge-cases")

    args = parser.parse_args(argv)
    if args.cmd == "assist-card":
        evidence = _load_json(args.evidence_json) if args.evidence_json else None
        out = build_assist_card(args.mode, args.observation, evidence, args.parked_reminder_state)
    elif args.cmd == "automation-outcome":
        out = explain_automation_outcome(_load_json(args.event_json) or {})
    elif args.cmd == "safety-digest":
        out = build_safety_digest(_load_json(args.signals_json) or [], period=args.period)
    elif args.cmd == "signal-inventory":
        out = load_signal_inventory()
    elif args.cmd == "vision-edge-cases":
        out = load_vision_edge_cases()
    else:  # pragma: no cover
        parser.error("unknown command")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
