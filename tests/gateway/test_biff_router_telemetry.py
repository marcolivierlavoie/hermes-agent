from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from gateway.biff_router_telemetry import (
    build_biff_router_telemetry_context,
    load_biff_router_events,
    record_biff_router_turn,
    render_biff_router_feedback_report,
    sanitize_for_router_telemetry,
    summarize_biff_router_events,
)


CONFIGURED = ["terminal", "file", "memory", "session_search", "todo", "kanban", "web", "search", "browser"]


def test_router_telemetry_context_records_route_memory_tier_and_fallback(monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "1")

    context = build_biff_router_telemetry_context(
        config={},
        platform_key="discord",
        message="Continue BIF-1526 and keep role-consent policy intact.",
        configured_toolsets=CONFIGURED,
        selected_toolsets=CONFIGURED,
    )

    assert context.router_enabled is True
    assert context.route == "conservative_full"
    assert context.fallback_full_surface is True
    assert context.memory_tier == "compact-memory"
    assert context.runtime in {"workflow", "continuation"}
    assert context.confidence == "low"


def test_record_router_turn_sanitizes_prompt_secret_and_memory_content(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_BIFF_TOOLSET_ROUTER", "1")
    path = tmp_path / "router.jsonl"
    context = build_biff_router_telemetry_context(
        config={},
        platform_key="discord",
        message="Remember this: prefer Tailnet URLs for homelab bookmarks.",
        configured_toolsets=CONFIGURED,
        selected_toolsets=["memory", "session_search", "terminal"],
    )

    raw_message = "Remember this: sk-secret-looking-token-1234567890 and private memory details"
    record_biff_router_turn(
        platform="discord",
        chat_id="chat-1",
        session_id="sess-1",
        message=raw_message,
        telemetry=context,
        model="gpt-test",
        provider="openai",
        tool_schema_chars=1234,
        last_prompt_tokens=567,
        input_tokens=600,
        output_tokens=40,
        wall_time=1.25,
        api_calls=2,
        outcome="final_response",
        recall_events=[{"kind": "toolset_recall_on_miss", "missing_tool": "terminal", "content": "raw memory content"}],
        path=path,
        now=1000.0,
    )

    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["message"]["chars"] == len(raw_message)
    assert "sha256_16" in row["message"]
    assert "Remember this" not in json.dumps(row)
    assert "raw memory content" not in json.dumps(row)
    assert "sk-secret" not in json.dumps(row)
    assert row["memory_tier"] == "normal-memory"
    assert row["recall_on_miss_count"] == 1
    assert row["selected_toolsets"] == ["memory", "session_search", "terminal"]


def test_sanitize_router_telemetry_redacts_secret_keys_and_fingerprints_raw_text():
    sanitized = sanitize_for_router_telemetry(
        {
            "api_key": "sk-test-should-not-appear-1234567890",
            "prompt": "raw private prompt",
            "nested": {"memory": "private memory text"},
        }
    )

    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["prompt"]["chars"] == len("raw private prompt")
    assert sanitized["nested"]["memory"]["chars"] == len("private memory text")
    assert "private" not in json.dumps(sanitized)
    assert "sk-test" not in json.dumps(sanitized)


def test_router_feedback_report_compares_baseline_and_router_enabled(tmp_path):
    path = tmp_path / "router.jsonl"
    base_context = {
        "route": "conservative_full",
        "confidence": "low",
        "runtime": "workflow",
        "action": "route_bundle",
        "toolset_profile": "full",
        "memory_tier": "compact-memory",
        "router_enabled": False,
        "fallback_full_surface": True,
        "selected_toolsets": CONFIGURED,
        "configured_toolsets_count": len(CONFIGURED),
    }
    router_context = {
        "route": "terminal",
        "confidence": "high",
        "runtime": "quick_terminal",
        "action": "one_tool",
        "toolset_profile": "terminal",
        "memory_tier": "compact-memory",
        "router_enabled": True,
        "fallback_full_surface": False,
        "selected_toolsets": ["terminal", "file"],
        "configured_toolsets_count": len(CONFIGURED),
    }
    record_biff_router_turn(
        platform="discord",
        chat_id="chat",
        session_id="baseline",
        message="baseline turn",
        telemetry=base_context,
        model="model-a",
        provider="openai",
        tool_schema_chars=9000,
        last_prompt_tokens=8000,
        wall_time=4.0,
        outcome="final_response",
        path=path,
        now=1.0,
    )
    record_biff_router_turn(
        platform="discord",
        chat_id="chat",
        session_id="router",
        message="what time is it",
        telemetry=router_context,
        model="model-a",
        provider="openai",
        tool_schema_chars=2000,
        last_prompt_tokens=3000,
        wall_time=1.0,
        outcome="final_response",
        path=path,
        now=2.0,
    )

    events = load_biff_router_events(path=path)
    summary = summarize_biff_router_events(events)
    report = render_biff_router_feedback_report(path=path)

    assert summary["baseline"]["count"] == 1
    assert summary["router_enabled"]["count"] == 1
    assert summary["baseline"]["avg_tool_schema_chars"] == 9000
    assert summary["router_enabled"]["avg_tool_schema_chars"] == 2000
    assert "baseline/off: 1 turns" in report
    assert "router/on: 1 turns" in report
    assert "routes: conservative_full=1, terminal=1" in report
    assert "privacy: stored prompt fingerprints only" in report


def test_router_feedback_report_script_outputs_text_and_json(tmp_path):
    path = tmp_path / "router.jsonl"
    record_biff_router_turn(
        platform="discord",
        chat_id="chat",
        session_id="router",
        message="what time is it",
        telemetry={
            "route": "terminal",
            "confidence": "high",
            "runtime": "quick_terminal",
            "action": "one_tool",
            "toolset_profile": "terminal",
            "memory_tier": "compact-memory",
            "router_enabled": True,
            "fallback_full_surface": False,
            "selected_toolsets": ["terminal", "file"],
            "configured_toolsets_count": 9,
        },
        wall_time=1.0,
        path=path,
        now=3.0,
    )

    text = subprocess.run(
        [sys.executable, "scripts/router_feedback_report.py", "--path", str(path), "--limit", "5"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    data = subprocess.run(
        [sys.executable, "scripts/router_feedback_report.py", "--path", str(path), "--limit", "5", "--json"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout

    assert "Biff tool-router feedback" in text
    assert json.loads(data)["router_enabled"]["count"] == 1
