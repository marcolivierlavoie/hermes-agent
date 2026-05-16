#!/usr/bin/env python3
"""Offline Mnemosyne recall evaluation runner.

This is intentionally provider-neutral: production recall systems can export JSONL
predictions with `case_id` and `recall_ids`, while this script owns the stable
Biff production acceptance pack and metrics definitions. It does not modify or
enable any production memory configuration.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class RecallCase:
    id: str
    category: str
    prompt: str
    expected_recall_ids: set[str]
    forbidden_recall_ids: set[str] = field(default_factory=set)
    stale_recall_ids: set[str] = field(default_factory=set)
    conflict_recall_ids: set[str] = field(default_factory=set)
    requires_source_verification: bool = False
    sensitive_risky: bool = False
    notes: str = ""


@dataclass(frozen=True)
class Prediction:
    case_id: str
    recall_ids: list[str]
    user_correction_proxy: bool = False


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path} line {line_number}: {exc}") from exc
    return rows


def load_cases(path: str | Path) -> list[RecallCase]:
    cases: list[RecallCase] = []
    seen: set[str] = set()
    for row in _read_jsonl(Path(path)):
        case_id = str(row["id"])
        if case_id in seen:
            raise ValueError(f"Duplicate case id: {case_id}")
        seen.add(case_id)
        cases.append(
            RecallCase(
                id=case_id,
                category=str(row["category"]),
                prompt=str(row["prompt"]),
                expected_recall_ids=set(row.get("expected_recall_ids", [])),
                forbidden_recall_ids=set(row.get("forbidden_recall_ids", [])),
                stale_recall_ids=set(row.get("stale_recall_ids", [])),
                conflict_recall_ids=set(row.get("conflict_recall_ids", [])),
                requires_source_verification=bool(row.get("requires_source_verification", False)),
                sensitive_risky=bool(row.get("sensitive_risky", False)),
                notes=str(row.get("notes", "")),
            )
        )
    return cases


def load_predictions(path: str | Path) -> dict[str, Prediction]:
    predictions: dict[str, Prediction] = {}
    for row in _read_jsonl(Path(path)):
        case_id = str(row["case_id"])
        if case_id in predictions:
            raise ValueError(f"Duplicate prediction case_id: {case_id}")
        predictions[case_id] = Prediction(
            case_id=case_id,
            recall_ids=[str(item) for item in row.get("recall_ids", [])],
            user_correction_proxy=bool(row.get("user_correction_proxy", False)),
        )
    return predictions


def oracle_predictions(cases: list[RecallCase]) -> dict[str, Prediction]:
    """Return perfect predictions so the pack can smoke-test without a provider."""
    return {
        case.id: Prediction(case_id=case.id, recall_ids=sorted(case.expected_recall_ids))
        for case in cases
    }


def _known_recall_ids(cases: list[RecallCase]) -> set[str]:
    known: set[str] = set()
    for case in cases:
        known.update(case.expected_recall_ids)
        known.update(case.forbidden_recall_ids)
        known.update(case.stale_recall_ids)
        known.update(case.conflict_recall_ids)
    return known


def _dedupe_sorted(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _map_memory_to_recall_ids(memory: dict[str, Any], known_ids: set[str]) -> list[str]:
    mapped: list[str] = []
    memory_id = str(memory.get("id") or "")
    topic = str(memory.get("topic") or "")
    if memory_id in known_ids:
        mapped.append(memory_id)
    if topic in known_ids:
        mapped.append(topic)
    return _dedupe_sorted(mapped)


def export_mnemosyne_predictions(
    cases: list[RecallCase],
    output_path: str | Path,
    *,
    hermes_home: str | Path | None = None,
    session_id: str = "mnemosyne-recall-eval",
) -> dict[str, Prediction]:
    """Run real Mnemosyne recall/prefetch paths and write eval predictions JSONL.

    This helper intentionally only instantiates the provider against the supplied
    current or fixture hermes_home. It does not enable production config. For
    sensitive/risky eval prompts, provider recall/trace are still exercised for
    coverage, but exported recall_ids fail closed to an empty list.
    """
    from plugins.memory.mnemosyne import MnemosyneProvider

    provider = MnemosyneProvider()
    init_kwargs: dict[str, Any] = {}
    if hermes_home is not None:
        init_kwargs["hermes_home"] = str(hermes_home)
    provider.initialize(session_id, **init_kwargs)

    known_ids = _known_recall_ids(cases)
    predictions: dict[str, Prediction] = {}
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8") as handle:
        for case in cases:
            trace = provider.prefetch_trace(case.prompt, session_id=session_id)
            recalled = provider.recall(case.prompt, limit=20)
            provider_memory_ids: list[str] = []
            provider_topics: list[str] = []
            mapped_ids: list[str] = []
            for item in recalled:
                memory = item.get("memory") if isinstance(item, dict) else None
                if not isinstance(memory, dict):
                    continue
                memory_id = str(memory.get("id") or "")
                topic = str(memory.get("topic") or "")
                if memory_id:
                    provider_memory_ids.append(memory_id)
                if topic:
                    provider_topics.append(topic)
                mapped_ids.extend(_map_memory_to_recall_ids(memory, known_ids))

            fail_closed = case.sensitive_risky
            recall_ids = [] if fail_closed else _dedupe_sorted(mapped_ids)
            prediction = Prediction(case_id=case.id, recall_ids=recall_ids)
            predictions[case.id] = prediction
            handle.write(json.dumps({
                "case_id": case.id,
                "recall_ids": prediction.recall_ids,
                "provider_memory_ids": _dedupe_sorted(provider_memory_ids),
                "provider_topics": _dedupe_sorted(provider_topics),
                "prefetch_injected": bool(trace.get("injected")),
                "prefetch_skip_reason": "" if trace.get("injected") else str(trace.get("skip_reason") or ""),
                "sensitive_risky_fail_closed": fail_closed,
                "user_correction_proxy": prediction.user_correction_proxy,
            }, ensure_ascii=False, sort_keys=True) + "\n")

    return predictions


def _round(value: float) -> float:
    return round(value, 2)


def evaluate(cases: list[RecallCase], predictions: dict[str, Prediction]) -> dict[str, Any]:
    total_selected = true_positive = false_positive = stale_selected = 0
    conflict_cases = conflict_skips = correction_cases = 0
    rows: list[dict[str, Any]] = []

    for case in cases:
        prediction = predictions.get(case.id, Prediction(case_id=case.id, recall_ids=[]))
        selected = set(prediction.recall_ids)
        expected = case.expected_recall_ids
        forbidden = case.forbidden_recall_ids | case.stale_recall_ids | case.conflict_recall_ids
        stale_hits = selected & case.stale_recall_ids
        conflict_hits = selected & case.conflict_recall_ids
        tp = len(selected & expected)
        fp = len(selected - expected) + len(selected & forbidden & expected)

        total_selected += len(selected)
        true_positive += tp
        false_positive += fp
        stale_selected += len(stale_hits)

        if case.conflict_recall_ids:
            conflict_cases += 1
            if not conflict_hits:
                conflict_skips += 1

        needs_correction = bool(
            prediction.user_correction_proxy
            or stale_hits
            or conflict_hits
            or (selected & case.forbidden_recall_ids)
            or (selected and not selected <= expected)
        )
        correction_cases += int(needs_correction)
        rows.append(
            {
                "case_id": case.id,
                "category": case.category,
                "selected": sorted(selected),
                "true_positive": tp,
                "false_positive": fp,
                "stale_hits": sorted(stale_hits),
                "conflict_hits": sorted(conflict_hits),
                "needs_user_correction_proxy": needs_correction,
            }
        )

    precision = true_positive / total_selected if total_selected else 0.0
    false_positive_rate = false_positive / total_selected if total_selected else 0.0
    stale_recall_rate = stale_selected / total_selected if total_selected else 0.0
    conflict_skip_rate = conflict_skips / conflict_cases if conflict_cases else 1.0
    user_correction_proxy = correction_cases / len(cases) if cases else 0.0

    worst_cases = [row for row in rows if row["false_positive"] or row["needs_user_correction_proxy"]]
    return {
        "case_count": len(cases),
        "selected_recall_count": total_selected,
        "metrics": {
            "precision": _round(precision),
            "false_positive_rate": _round(false_positive_rate),
            "stale_recall_rate": _round(stale_recall_rate),
            "conflict_skip_rate": _round(conflict_skip_rate),
            "user_correction_proxy": _round(user_correction_proxy),
        },
        "raw_counts": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "stale_selected": stale_selected,
            "conflict_cases": conflict_cases,
            "conflict_skips": conflict_skips,
            "correction_cases": correction_cases,
        },
        "worst_cases": worst_cases,
        "cases": rows,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Mnemosyne recall predictions against the Biff production pack.")
    parser.add_argument("--cases", required=True, help="JSONL recall case fixture path")
    parser.add_argument("--predictions", help="JSONL predictions path; omitted uses oracle predictions for smoke tests")
    parser.add_argument("--export-mnemosyne-predictions", help="Write predictions JSONL from real MnemosyneProvider recall/prefetch paths")
    parser.add_argument("--mnemosyne-hermes-home", help="Current or fixture HERMES_HOME for MnemosyneProvider export mode")
    parser.add_argument("--output", help="Optional JSON report path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = load_cases(args.cases)
    if args.export_mnemosyne_predictions:
        predictions = export_mnemosyne_predictions(
            cases,
            args.export_mnemosyne_predictions,
            hermes_home=args.mnemosyne_hermes_home,
        )
    else:
        predictions = load_predictions(args.predictions) if args.predictions else oracle_predictions(cases)
    report = evaluate(cases, predictions)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
