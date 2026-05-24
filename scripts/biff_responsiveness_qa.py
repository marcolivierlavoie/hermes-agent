#!/usr/bin/env python3
"""Run the Biff responsiveness roadmap smoke QA."""

from __future__ import annotations

import argparse
import sys

from gateway.biff_responsiveness_qa import (
    FAIL,
    PENDING,
    gateway_launchd_state,
    results_as_json,
    run_responsiveness_qa,
    summarize_responsiveness_qa,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--card", action="append", help="limit to a card such as K-1299; repeatable")
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="exit non-zero when any card is pending, not just when a check fails",
    )
    parser.add_argument(
        "--gateway-state",
        action="store_true",
        help="include best-effort launchd state in the text report",
    )
    args = parser.parse_args(argv)

    results = run_responsiveness_qa(args.card)
    summary = summarize_responsiveness_qa(results)

    if args.json:
        print(results_as_json(results))
    else:
        if args.gateway_state:
            print(f"Gateway launchd state: {gateway_launchd_state()}")
        print(
            "Biff responsiveness QA: "
            f"{summary.get('pass', 0)} pass, {summary.get('fail', 0)} fail, "
            f"{summary.get('pending', 0)} pending"
        )
        for result in results:
            print(f"\n{result.card} [{result.status}] {result.title}")
            print(f"Usage: {result.real_life_usage}")
            print(f"Result: {result.detail}")

    if summary.get(FAIL, 0):
        return 1
    if args.require_all and summary.get(PENDING, 0):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
