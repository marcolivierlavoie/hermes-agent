#!/usr/bin/env python3
"""Generate a non-mutating Mnemosyne hygiene report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from plugins.memory.mnemosyne import MnemosyneProvider


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Mnemosyne hygiene report",
        "",
        f"Generated: {report['generated_at']}",
        f"Memory count: {report['memory_count']}",
        f"Suppressed count: {report['suppressed_count']}",
        f"Includes suppressed: {report['include_suppressed']}",
        f"Mutated: {report['mutated']}",
        "",
        "## Recommendations",
        "",
    ]
    if not report["recommendations"]:
        lines.append("No hygiene recommendations.")
    for idx, item in enumerate(report["recommendations"], 1):
        ids = ", ".join(item["candidate_memory_ids"])
        lines.extend([
            f"### {idx}. {item['suggested_action']}",
            "",
            f"- Memory IDs: `{ids}`",
            f"- Reason: {item['reason']}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME"), help="Hermes home/profile to inspect")
    parser.add_argument("--include-suppressed", action="store_true", help="Include suppressed memories in the hygiene scan")
    parser.add_argument("--json-output", type=Path, help="Write JSON report to this path")
    parser.add_argument("--markdown-output", type=Path, help="Write Markdown report to this path")
    args = parser.parse_args()

    provider = MnemosyneProvider()
    kwargs = {"hermes_home": args.hermes_home} if args.hermes_home else {}
    provider.initialize("mnemosyne-hygiene", **kwargs)
    report = provider.hygiene_report(include_suppressed=args.include_suppressed)

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(_markdown(report), encoding="utf-8")

    if not args.json_output and not args.markdown_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"recommendations={len(report['recommendations'])} mutated={report['mutated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
