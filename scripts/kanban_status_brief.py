#!/usr/bin/env python3
"""Print compact Kanban status/attention evidence for K-id questions."""
from __future__ import annotations

import argparse
import json

from gateway.biff_kanban_brief import build_kanban_brief, render_kanban_brief


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("refs", nargs="+", help="K-ids or internal task ids")
    parser.add_argument("--board", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--recent-limit", type=int, default=3)
    args = parser.parse_args()
    brief = build_kanban_brief(args.refs, board=args.board, recent_limit=max(1, args.recent_limit))
    if args.as_json:
        print(json.dumps(brief, indent=2, sort_keys=True))
    else:
        print(render_kanban_brief(brief))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
