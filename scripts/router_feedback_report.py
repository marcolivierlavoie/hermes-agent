#!/usr/bin/env python3
"""Render local Biff tool-router telemetry feedback.

Usage:
  python scripts/router_feedback_report.py [--json] [--limit 200] [--path FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.biff_router_telemetry import (
    load_biff_router_events,
    render_biff_router_feedback_report,
    summarize_biff_router_events,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Biff tool-router telemetry feedback")
    parser.add_argument("--path", type=Path, default=None, help="Telemetry JSONL path")
    parser.add_argument("--limit", type=int, default=200, help="Number of recent events to summarize")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary")
    args = parser.parse_args()

    if args.json:
        events = load_biff_router_events(path=args.path, limit=args.limit)
        print(json.dumps(summarize_biff_router_events(events), indent=2, sort_keys=True))
    else:
        print(render_biff_router_feedback_report(path=args.path, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
