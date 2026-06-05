#!/usr/bin/env python3
"""Summarize Biff Discord diagnostic JSONL traces."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        default="/Users/marco/.hermes/runtime/biff-discord-diagnostics.jsonl",
    )
    parser.add_argument("--last", type=int, default=0, help="Only inspect the last N rows")
    args = parser.parse_args()

    rows = load_rows(Path(args.path))
    if args.last > 0:
        rows = rows[-args.last :]
    print(f"rows: {len(rows)}")
    print("events:", dict(Counter(row.get("event") for row in rows)))

    turns: dict[str, dict] = defaultdict(dict)
    for row in rows:
        sid = row.get("session_id") or row.get("original_session_id")
        if not sid:
            continue
        event = row.get("event")
        if event == "turn_start":
            turns[sid].setdefault("starts", []).append(row)
        elif event == "prompt_prepared":
            turns[sid].setdefault("prompts", []).append(row)
        elif event == "turn_result":
            turns[sid].setdefault("results", []).append(row)
        elif event == "tool_policy":
            turns[sid].setdefault("tools", []).append(row)

    for sid, data in turns.items():
        results = data.get("results") or []
        prompts = data.get("prompts") or []
        tools = data.get("tools") or []
        starts = data.get("starts") or []
        if not (results or starts):
            continue
        times = [r.get("iso") for r in starts + results if r.get("iso")]
        print(f"\n{sid}")
        if times:
            print(f"  window: {min(times)} -> {max(times)}")
        print(f"  starts={len(starts)} prompts={len(prompts)} results={len(results)} tool_policy={len(tools)}")
        if results:
            api_calls = [int(r.get("api_calls") or 0) for r in results]
            prompt_tokens = [int(r.get("last_prompt_tokens") or 0) for r in results]
            print(
                "  api_calls avg/max="
                f"{statistics.mean(api_calls):.1f}/{max(api_calls)} "
                f"last_prompt max={max(prompt_tokens)}"
            )
            print("  exit reasons:", dict(Counter(r.get("turn_exit_reason") for r in results)))
        if prompts:
            omitted = [
                int((p.get("token_source_metrics") or {}).get("prompt_budget_omitted_messages") or 0)
                for p in prompts
            ]
            schema = [int(p.get("tool_schema_chars") or 0) for p in prompts]
            print(f"  prompt omitted max={max(omitted)} tool_schema values={sorted(set(schema))}")
        if tools:
            blocked = [t for t in tools if not t.get("allowed", True)]
            print(f"  blocked_tools={len(blocked)} reasons={dict(Counter(t.get('reason') for t in blocked))}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
