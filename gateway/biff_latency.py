"""Persisted Discord latency snapshots for Biff responsiveness reports."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from hermes_constants import get_hermes_home


DEFAULT_LIMIT = 50
MAX_STORED_RECORDS = 500
LATENCY_PATH = get_hermes_home() / "runtime" / "biff-discord-latency.jsonl"


def _safe_float(value: Any) -> float:
    try:
        return round(max(0.0, float(value or 0.0)), 3)
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _path(path: Path | None = None) -> Path:
    resolved = path or LATENCY_PATH
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def record_biff_latency(
    *,
    platform: str,
    chat_id: str | None,
    session_id: str | None,
    model: str | None,
    response_time: float,
    wall_metrics: Mapping[str, Any],
    api_calls: int = 0,
    response_chars: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    last_prompt_tokens: int = 0,
    tool_schema_chars: int = 0,
    prompt_budget_applied: int = 0,
    prompt_budget_tokens: int = 10_000,
    prompt_budget_omitted_messages: int = 0,
    path: Path | None = None,
    now: float | None = None,
) -> None:
    """Append one non-sensitive live-chat latency record."""

    if str(platform or "").strip().lower() != "discord":
        return
    metrics = wall_metrics if isinstance(wall_metrics, Mapping) else {}
    record = {
        "ts": float(time.time() if now is None else now),
        "platform": "discord",
        "chat_id": str(chat_id or "unknown")[:120],
        "session_id": str(session_id or "")[:120],
        "model": str(model or "")[:160],
        "response_time": _safe_float(response_time),
        "wall_time": _safe_float(metrics.get("wall_time")),
        "gateway_pre_agent_time": _safe_float(metrics.get("gateway_pre_agent_time")),
        "gateway_prep_time": _safe_float(metrics.get("gateway_prep_time")),
        "agent_loop_time": _safe_float(metrics.get("agent_loop_time")),
        "gateway_run_agent_overhead_time": _safe_float(metrics.get("gateway_run_agent_overhead_time")),
        "gateway_postprocess_time": _safe_float(metrics.get("gateway_postprocess_time")),
        "gateway_other_time": _safe_float(metrics.get("gateway_other_time")),
        "long_turn_elapsed": _safe_float(metrics.get("long_turn_elapsed")),
        "long_turn_tool_calls": _safe_int(metrics.get("long_turn_tool_calls")),
        "api_calls": _safe_int(api_calls),
        "response_chars": _safe_int(response_chars),
        "input_tokens": _safe_int(input_tokens),
        "output_tokens": _safe_int(output_tokens),
        "last_prompt_tokens": _safe_int(last_prompt_tokens),
        "tool_schema_chars": _safe_int(tool_schema_chars),
        "prompt_budget_applied": _safe_int(prompt_budget_applied),
        "prompt_budget_tokens": _safe_int(prompt_budget_tokens) or 10_000,
        "prompt_budget_omitted_messages": _safe_int(prompt_budget_omitted_messages),
    }
    dest = _path(path)
    try:
        existing = dest.read_text(encoding="utf-8").splitlines()[-(MAX_STORED_RECORDS - 1):]
    except FileNotFoundError:
        existing = []
    lines = [line for line in existing if line.strip()]
    lines.append(json.dumps(record, sort_keys=True, ensure_ascii=False))
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_biff_latency_records(*, path: Path | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    dest = _path(path)
    try:
        lines = dest.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict) and item.get("platform") == "discord":
            records.append(item)
    if limit:
        return records[-max(1, int(limit)):]
    return records


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    idx = (len(ordered) - 1) * percentile
    lower = int(idx)
    upper = min(lower + 1, len(ordered) - 1)
    weight = idx - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def _dominant_slow_phase(record: Mapping[str, Any]) -> str:
    phases = {
        "model/tool work": _safe_float(record.get("agent_loop_time")),
        "gateway prep": _safe_float(record.get("gateway_prep_time")),
        "before-agent queue/setup": _safe_float(record.get("gateway_pre_agent_time")),
        "gateway overhead": _safe_float(record.get("gateway_run_agent_overhead_time")),
        "post-processing": _safe_float(record.get("gateway_postprocess_time")),
        "other gateway time": _safe_float(record.get("gateway_other_time")),
    }
    name, seconds = max(phases.items(), key=lambda item: item[1])
    return f"{name} ({seconds:.1f}s)"


def summarize_biff_latency(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(r) for r in records]
    response_times = [_safe_float(r.get("response_time")) for r in rows]
    response_times = [value for value in response_times if value > 0]
    worst = sorted(rows, key=lambda r: _safe_float(r.get("response_time")), reverse=True)[:3]
    return {
        "count": len(rows),
        "p50": round(statistics.median(response_times), 3) if response_times else 0.0,
        "p95": _percentile(response_times, 0.95),
        "max": max(response_times) if response_times else 0.0,
        "avg": round(statistics.mean(response_times), 3) if response_times else 0.0,
        "worst": worst,
    }


def render_biff_latency_report(*, path: Path | None = None, limit: int = DEFAULT_LIMIT) -> str:
    records = load_biff_latency_records(path=path, limit=limit)
    summary = summarize_biff_latency(records)
    if not records:
        return (
            "No Discord speed samples have been recorded yet. Ask Biff a few "
            "questions, then run `/speed` again."
        )
    target_tokens = max(1, int(records[-1].get("prompt_budget_tokens") or 10_000))
    prompt_token_records = [int(record.get("last_prompt_tokens") or 0) for record in records]
    prompt_token_records = [tokens for tokens in prompt_token_records if tokens > 0]
    within_target = sum(1 for tokens in prompt_token_records if tokens <= target_tokens)
    latest_prompt_tokens = prompt_token_records[-1] if prompt_token_records else 0
    budgeted_turns = sum(1 for record in records if int(record.get("prompt_budget_applied") or 0) > 0)
    omitted_messages = sum(int(record.get("prompt_budget_omitted_messages") or 0) for record in records)
    lines = [
        f"Biff Discord speed, last {summary['count']} turns:",
        f"- p50: {summary['p50']:.1f}s",
        f"- p95: {summary['p95']:.1f}s",
        f"- average: {summary['avg']:.1f}s",
        f"- worst: {summary['max']:.1f}s",
    ]
    if prompt_token_records:
        lines.append(
            f"- prompt target: {within_target}/{len(prompt_token_records)} recent turns <= "
            f"{target_tokens:,} tokens (latest: {latest_prompt_tokens:,})"
        )
    if budgeted_turns:
        lines.append(f"- live prompt budget trimmed {omitted_messages} old messages across {budgeted_turns} turns")
    if summary["worst"]:
        lines.append("")
        lines.append("Slowest recent turns:")
        for record in summary["worst"]:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(record.get("ts") or 0)))
            lines.append(
                f"- {ts}: {float(record.get('response_time') or 0):.1f}s, "
                f"mostly {_dominant_slow_phase(record)}, "
                f"{int(record.get('api_calls') or 0)} API calls, "
                f"{int(record.get('last_prompt_tokens') or 0):,} prompt tokens"
            )
    return "\n".join(lines)
