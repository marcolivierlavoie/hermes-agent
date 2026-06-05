from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:  # Prefer PyYAML when the Hermes venv is active.
    import yaml  # type: ignore
except ModuleNotFoundError:  # Keep the CLI dependency-light under /usr/bin/env python3.
    yaml = None

DATA_DIR = Path(__file__).with_name("data")


def _load_yamlish(path: str | Path) -> dict[str, Any]:
    """Load the tiny repository-owned YAML subset without requiring PyYAML.

    The data files intentionally use a small shape: top-level list key, list
    items with scalar fields, and occasional inline/list arrays.
    """
    text = Path(path).read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text) or {}
    root_key: str | None = None
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending_list_key: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.strip()
        if not raw.startswith(" ") and stripped.endswith(":"):
            root_key = stripped[:-1]
            continue
        if stripped.startswith("- ") and ":" in stripped[2:]:
            current = {}
            rows.append(current)
            pending_list_key = None
            key, value = stripped[2:].split(":", 1)
            current[key.strip()] = _parse_scalar(value.strip())
            continue
        if current is None:
            continue
        if stripped.startswith("- ") and pending_list_key:
            current.setdefault(pending_list_key, []).append(stripped[2:].strip())
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value:
                current[key] = _parse_scalar(value)
                pending_list_key = None
            else:
                current[key] = []
                pending_list_key = key
    return {root_key or "items": rows}


def _parse_scalar(value: str) -> Any:
    if value in {"true", "false"}:
        return value == "true"
    if value.isdigit():
        return int(value)
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [item.strip() for item in inner.split(",")] if inner else []
    return value


def load_signal_inventory(path: str | Path | None = None) -> dict[str, Any]:
    return _load_yamlish(path or DATA_DIR / "safety_signals.yaml") or {"signals": []}


def load_vision_edge_cases(path: str | Path | None = None) -> dict[str, Any]:
    return _load_yamlish(path or DATA_DIR / "vision_edge_cases.yaml") or {"edge_cases": []}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _age_minutes(observed_at: str | None, now: datetime) -> int | None:
    ts = _parse_ts(observed_at)
    if not ts:
        return None
    return int((now - ts).total_seconds() // 60)


def build_safety_digest(
    signals: list[Mapping[str, Any]],
    period: str = "morning",
    now: datetime | None = None,
    inventory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a low-noise morning/evening household safety digest.

    Input signals are snapshots from HA/n8n/car/manual sources. The function does not poll
    privileged systems itself; it normalizes evidence already owned by those subsystems.
    """
    now = now or datetime.now(timezone.utc)
    inventory = inventory or load_signal_inventory()
    by_id = {s["id"]: s for s in inventory.get("signals", [])}

    items: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for raw in signals:
        signal_id = str(raw.get("id") or raw.get("signal") or "unknown")
        spec = by_id.get(signal_id, {})
        observed_at = raw.get("observed_at") or raw.get("last_changed")
        age = _age_minutes(observed_at, now)
        max_age = int(raw.get("freshness_minutes") or spec.get("freshness_minutes") or 1440)
        stale = age is None or age > max_age
        severity = str(raw.get("severity") or "info").lower()
        status = str(raw.get("status") or raw.get("state") or "unknown")
        should_surface = bool(raw.get("surface")) or severity in {"critical", "warning"} or stale
        row = {
            "id": signal_id,
            "title": raw.get("title") or signal_id.replace("_", " "),
            "status": status,
            "severity": severity,
            "observed_at": observed_at,
            "freshness": "unknown/stale" if age is None else ("fresh" if not stale else f"stale ({age}m old)"),
            "evidence": raw.get("evidence") or [],
            "recommended_action": raw.get("recommended_action") or _default_action(signal_id, status, stale),
            "source": raw.get("source") or spec.get("source") or "unknown",
        }
        if should_surface:
            items.append(row)
        else:
            suppressed.append({"id": signal_id, "reason": "nominal signal suppressed for low-noise digest"})

    order = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda x: (order.get(x["severity"], 3), x["title"]))
    return {
        "digest_type": "household-safety-digest/v1",
        "period": period,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "summary": _summary(items),
        "items": items,
        "suppressed_count": len(suppressed),
        "suppressed": suppressed[:10],
    }


def _default_action(signal_id: str, status: str, stale: bool) -> str:
    if stale:
        return "Refresh evidence before interrupting Marco unless other risk is present."
    if "door" in signal_id or "window" in signal_id or "sunroof" in signal_id:
        return "Check/close if this conflicts with weather, sleep, or away state."
    if "fuel" in signal_id or "service" in signal_id:
        return "Plan fuel/service only if needed before next expected drive."
    if "automation" in signal_id:
        return "Open the automation outcome explainer and inspect the failed gate/evidence."
    return "No action unless Marco asks."


def _summary(items: list[Mapping[str, Any]]) -> str:
    if not items:
        return "No household safety signals need attention."
    critical = sum(1 for item in items if item.get("severity") == "critical")
    warning = sum(1 for item in items if item.get("severity") == "warning")
    return f"{len(items)} signal(s) surfaced: {critical} critical, {warning} warning."
