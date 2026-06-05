#!/usr/bin/env python3
"""Run Hermes synthetic checks for live Biff behavior."""

from __future__ import annotations

import argparse
import sys

from gateway.biff_synthetic_checks import (
    FAIL,
    run_synthetic_checks,
    summarize_synthetic_checks,
    synthetic_results_as_json,
    write_synthetic_artifact,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", help="run one scenario by name; repeatable")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--write-artifact", action="store_true", help="write a JSON artifact under ~/.hermes/runtime/synthetic-checks")
    args = parser.parse_args(argv)

    results = run_synthetic_checks(args.scenario)
    summary = summarize_synthetic_checks(results)
    artifact = write_synthetic_artifact(results) if args.write_artifact else None

    if args.json:
        print(synthetic_results_as_json(results))
    else:
        print(f"Hermes synthetic checks: {summary.get('pass', 0)} pass, {summary.get('fail', 0)} fail")
        if artifact:
            print(f"Artifact: {artifact}")
        for result in results:
            print(f"\n{result.name} [{result.status}]")
            print(result.detail)
            print(f"Observed: action={result.observed.get('action')} runtime={result.observed.get('runtime')} tools={result.observed.get('enabled_toolsets')}")

    return 1 if summary.get(FAIL, 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
