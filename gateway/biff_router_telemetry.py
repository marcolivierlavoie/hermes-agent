"""Sanitized telemetry and local reporting for Biff tool-router decisions."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from hermes_constants import get_hermes_home

MAX_STORED_RECORDS = 1000
DEFAULT_LIMIT = 200
TELEMETRY_PATH = get_hermes_home() / "runtime" / "biff-tool-router-telemetry.jsonl"

_SECRET_KEY_FRAGMENTS = ("api", "key", "token", "secret", "password", "credential", "auth", "cookie")


@dataclass(frozen=True)
class BiffRouterTelemetryContext:
    route: str
    confidence: str
    runtime: str
    action: str
    toolset_profile: str
    selected_toolsets: tuple[str, ...]
    configured_toolsets: tuple[str, ...]
    memory_tier: str
    router_enabled: bool
    fallback_full_surface: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "confidence": self.confidence,
            "runtime": self.runtime,
            "action": self.action,
            "toolset_profile": self.toolset_profile,
            "selected_toolsets": list(self.selected_toolsets),
            "configured_toolsets_count": len(self.configured_toolsets),
            "memory_tier": self.memory_tier,
            "router_enabled": self.router_enabled,
            "fallback_full_surface": self.fallback_full_surface,
            "reason": self.reason,
        }


def _path(path: Path | None = None) -> Path:
    resolved = path or TELEMETRY_PATH
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return round(max(0.0, float(value or 0.0)), 3)
    except Exception:
        return 0.0


def _safe_str(value: Any, *, max_chars: int = 160) -> str:
    text = str(value or "")
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def _sorted_unique(values: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(str(value) for value in (values or []) if str(value).strip())))


def _looks_secret_key(key: str) -> bool:
    lowered = str(key or "").lower()
    return any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS)


def sanitize_for_router_telemetry(value: Any, *, max_string_chars: int = 240) -> Any:
    """Return a JSON-safe value without raw prompts, memory content, or secrets."""

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _looks_secret_key(key):
                sanitized[key] = "[REDACTED]"
            elif key in {"message", "prompt", "query", "content", "memory", "raw_prompt", "final_response"}:
                if isinstance(raw_value, Mapping) and {"chars", "sha256_16"}.issubset(set(raw_value.keys())):
                    sanitized[key] = sanitize_for_router_telemetry(raw_value, max_string_chars=max_string_chars)
                else:
                    sanitized[key] = fingerprint_text(raw_value)
            else:
                sanitized[key] = sanitize_for_router_telemetry(raw_value, max_string_chars=max_string_chars)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_for_router_telemetry(item, max_string_chars=max_string_chars) for item in list(value)[:50]]
    if isinstance(value, str):
        if len(value) > max_string_chars:
            return value[: max_string_chars - 20] + f"…[+{len(value) - max_string_chars} chars]"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _safe_str(value, max_chars=max_string_chars)


def fingerprint_text(value: Any) -> dict[str, Any]:
    text = str(value or "")
    return {
        "chars": len(text),
        "sha256_16": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16],
    }


def _confidence_for_plan(plan: Any, route: str, fallback: bool) -> str:
    action = str(getattr(plan, "action", "") or "")
    runtime = str(getattr(plan, "runtime", "") or "")
    if fallback:
        return "low"
    if action in {"answer_now", "one_tool"} or route in {"none", "terminal", "kanban", "memory", "web", "vision"}:
        return "high"
    if runtime in {"direct_answer", "tool_access_recovery", "memory_lookup", "kanban_read"}:
        return "high"
    return "medium"


def build_biff_router_telemetry_context(
    *,
    config: Mapping[str, Any] | None,
    platform_key: str | None,
    message: Any,
    configured_toolsets: Iterable[str] | None,
    selected_toolsets: Iterable[str] | None,
) -> BiffRouterTelemetryContext:
    from agent.biff_intent_router import plan_biff_turn
    from gateway.biff_memory_tiers import classify_biff_memory_tier
    from gateway.biff_toolset_router import biff_toolset_router_enabled, route_class_for_plan

    plan = plan_biff_turn(message, command=False)
    route = route_class_for_plan(plan)
    configured = _sorted_unique(configured_toolsets)
    selected = _sorted_unique(selected_toolsets)
    router_enabled = biff_toolset_router_enabled(config, platform_key)
    fallback_full_surface = route == "conservative_full" or set(selected) == set(configured)
    memory_tier = classify_biff_memory_tier(message, plan)
    return BiffRouterTelemetryContext(
        route=route,
        confidence=_confidence_for_plan(plan, route, fallback_full_surface),
        runtime=str(getattr(plan, "runtime", "") or ""),
        action=str(getattr(plan, "action", "") or ""),
        toolset_profile=str(getattr(plan, "toolset_profile", "") or ""),
        selected_toolsets=selected,
        configured_toolsets=configured,
        memory_tier=memory_tier.tier,
        router_enabled=router_enabled,
        fallback_full_surface=fallback_full_surface,
        reason=str(getattr(plan, "reason", "") or "")[:240],
    )


def record_biff_router_turn(
    *,
    platform: str | None,
    chat_id: str | None,
    session_id: str | None,
    message: Any = None,
    telemetry: BiffRouterTelemetryContext | Mapping[str, Any] | None,
    model: str | None = None,
    provider: str | None = None,
    tool_schema_chars: int = 0,
    last_prompt_tokens: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    wall_time: float = 0.0,
    api_calls: int = 0,
    outcome: str = "unknown",
    recall_events: Iterable[Mapping[str, Any]] | None = None,
    path: Path | None = None,
    now: float | None = None,
) -> None:
    """Append one sanitized router telemetry event.

    The record intentionally stores a prompt fingerprint only, never prompt text or
    memory content.  It is safe for local inspection and report generation.
    """

    if str(platform or "").strip().lower() != "discord":
        return
    ctx = telemetry.to_dict() if isinstance(telemetry, BiffRouterTelemetryContext) else dict(telemetry or {})
    recalls = [sanitize_for_router_telemetry(event) for event in list(recall_events or [])[:10]]
    record = {
        "ts": float(time.time() if now is None else now),
        "platform": "discord",
        "chat_id": _safe_str(chat_id or "unknown", max_chars=120),
        "session_id": _safe_str(session_id or "", max_chars=120),
        "message": fingerprint_text(message),
        "route": _safe_str(ctx.get("route"), max_chars=80),
        "confidence": _safe_str(ctx.get("confidence"), max_chars=32),
        "runtime": _safe_str(ctx.get("runtime"), max_chars=80),
        "action": _safe_str(ctx.get("action"), max_chars=80),
        "toolset_profile": _safe_str(ctx.get("toolset_profile"), max_chars=80),
        "memory_tier": _safe_str(ctx.get("memory_tier"), max_chars=80),
        "router_enabled": bool(ctx.get("router_enabled")),
        "fallback_full_surface": bool(ctx.get("fallback_full_surface")),
        "selected_toolsets": _sorted_unique(ctx.get("selected_toolsets") or ()),
        "configured_toolsets_count": _safe_int(ctx.get("configured_toolsets_count")),
        "recall_on_miss_count": len(recalls),
        "recall_on_miss": recalls,
        "model": _safe_str(model, max_chars=160),
        "provider": _safe_str(provider, max_chars=80),
        "tool_schema_chars": _safe_int(tool_schema_chars),
        "last_prompt_tokens": _safe_int(last_prompt_tokens),
        "input_tokens": _safe_int(input_tokens),
        "output_tokens": _safe_int(output_tokens),
        "wall_time": _safe_float(wall_time),
        "api_calls": _safe_int(api_calls),
        "outcome": _safe_str(outcome, max_chars=80),
    }
    record = sanitize_for_router_telemetry(record)
    dest = _path(path)
    try:
        existing = dest.read_text(encoding="utf-8").splitlines()[-(MAX_STORED_RECORDS - 1) :]
    except FileNotFoundError:
        existing = []
    lines = [line for line in existing if line.strip()]
    lines.append(json.dumps(record, sort_keys=True, ensure_ascii=False, default=str))
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_biff_router_events(*, path: Path | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    dest = _path(path)
    try:
        lines = dest.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("platform") == "discord":
            events.append(row)
    if limit:
        return events[-max(1, int(limit)) :]
    return events


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


def _group_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    wall = [_safe_float(r.get("wall_time")) for r in rows if _safe_float(r.get("wall_time")) > 0]
    schema = [_safe_int(r.get("tool_schema_chars")) for r in rows if _safe_int(r.get("tool_schema_chars")) > 0]
    prompts = [_safe_int(r.get("last_prompt_tokens")) for r in rows if _safe_int(r.get("last_prompt_tokens")) > 0]
    return {
        "count": len(rows),
        "p50_wall_time": round(statistics.median(wall), 3) if wall else 0.0,
        "p95_wall_time": _percentile(wall, 0.95),
        "avg_wall_time": round(statistics.mean(wall), 3) if wall else 0.0,
        "avg_tool_schema_chars": round(statistics.mean(schema), 1) if schema else 0.0,
        "avg_last_prompt_tokens": round(statistics.mean(prompts), 1) if prompts else 0.0,
        "fallback_full_surface_count": sum(1 for r in rows if r.get("fallback_full_surface")),
        "recall_on_miss_count": sum(_safe_int(r.get("recall_on_miss_count")) for r in rows),
        "routes": dict(Counter(str(r.get("route") or "unknown") for r in rows)),
        "memory_tiers": dict(Counter(str(r.get("memory_tier") or "unknown") for r in rows)),
        "outcomes": dict(Counter(str(r.get("outcome") or "unknown") for r in rows)),
    }


def summarize_biff_router_events(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(event) for event in events]
    baseline = [row for row in rows if not row.get("router_enabled")]
    router = [row for row in rows if row.get("router_enabled")]
    return {
        "count": len(rows),
        "baseline": _group_summary(baseline),
        "router_enabled": _group_summary(router),
        "all": _group_summary(rows),
    }


def _format_counter(counter: Mapping[str, Any], *, limit: int = 5) -> str:
    if not counter:
        return "none"
    items = sorted(counter.items(), key=lambda item: (-_safe_int(item[1]), str(item[0])))[:limit]
    return ", ".join(f"{key}={value}" for key, value in items)


def render_biff_router_feedback_report(*, path: Path | None = None, limit: int = DEFAULT_LIMIT) -> str:
    events = load_biff_router_events(path=path, limit=limit)
    if not events:
        return "No Biff tool-router telemetry has been recorded yet."
    summary = summarize_biff_router_events(events)
    baseline = summary["baseline"]
    router = summary["router_enabled"]
    all_rows = summary["all"]
    lines = [
        f"Biff tool-router feedback, last {summary['count']} Discord turns:",
        f"- baseline/off: {baseline['count']} turns, p50 {baseline['p50_wall_time']:.1f}s, avg schema {baseline['avg_tool_schema_chars']:.0f} chars, avg prompt {baseline['avg_last_prompt_tokens']:.0f} tokens",
        f"- router/on: {router['count']} turns, p50 {router['p50_wall_time']:.1f}s, avg schema {router['avg_tool_schema_chars']:.0f} chars, avg prompt {router['avg_last_prompt_tokens']:.0f} tokens",
        f"- all outcomes: {_format_counter(all_rows['outcomes'])}",
        f"- routes: {_format_counter(all_rows['routes'])}",
        f"- memory tiers: {_format_counter(all_rows['memory_tiers'])}",
        f"- fallback/full-surface turns: {all_rows['fallback_full_surface_count']}",
        f"- recall-on-miss events: {all_rows['recall_on_miss_count']}",
        "- privacy: stored prompt fingerprints only; raw prompts, memory content, and secret-like fields are omitted/redacted.",
    ]
    return "\n".join(lines)


__all__ = [
    "BiffRouterTelemetryContext",
    "TELEMETRY_PATH",
    "build_biff_router_telemetry_context",
    "fingerprint_text",
    "load_biff_router_events",
    "record_biff_router_turn",
    "render_biff_router_feedback_report",
    "sanitize_for_router_telemetry",
    "summarize_biff_router_events",
]
