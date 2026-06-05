from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.toolset_recall import recall_toolsets_for_missing_tool, widen_agent_tools_for_missing_tool


def _tool_schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


@pytest.mark.parametrize(
    ("tool_name", "source_toolset", "current", "ceiling", "expected_added"),
    [
        ("web_search", "web", ["memory"], ["memory", "web", "search", "browser", "terminal", "file"], ["browser", "file", "search", "terminal", "web"]),
        ("terminal", "terminal", ["memory"], ["memory", "terminal", "file"], ["file", "terminal"]),
        ("kanban_show", "kanban", ["memory"], ["memory", "kanban", "terminal"], ["kanban", "terminal"]),
        ("mnemosyne_memory", "memory", ["todo"], ["todo", "memory", "session_search", "terminal"], ["memory", "session_search", "terminal"]),
    ],
)
def test_recall_toolsets_for_missing_tool_covers_required_synthetic_scenarios(
    monkeypatch,
    tool_name,
    source_toolset,
    current,
    ceiling,
    expected_added,
):
    """BIF-1524 requires web, terminal, Kanban, and memory recall coverage."""

    monkeypatch.setattr(
        "model_tools.get_toolset_for_tool",
        lambda requested: source_toolset if requested == tool_name else None,
    )

    widened, event = recall_toolsets_for_missing_tool(
        tool_name,
        current_toolsets=current,
        ceiling_toolsets=ceiling,
    )

    assert event is not None
    assert event["kind"] == "toolset_recall_on_miss"
    assert event["missing_tool"] == tool_name
    assert event["source_toolset"] == source_toolset
    assert event["added_toolsets"] == expected_added
    assert set(widened) == set(current) | set(expected_added)


def test_recall_toolsets_for_missing_tool_does_not_exceed_configured_ceiling(monkeypatch):
    monkeypatch.setattr(
        "model_tools.get_toolset_for_tool",
        lambda tool_name: "terminal" if tool_name == "terminal" else None,
    )

    widened, event = recall_toolsets_for_missing_tool(
        "terminal",
        current_toolsets=["memory"],
        ceiling_toolsets=["memory"],
    )

    assert widened == ["memory"]
    assert event is None


def test_recall_toolsets_for_missing_tool_respects_disabled_toolsets(monkeypatch):
    monkeypatch.setattr(
        "model_tools.get_toolset_for_tool",
        lambda tool_name: "terminal" if tool_name == "terminal" else None,
    )

    widened, event = recall_toolsets_for_missing_tool(
        "terminal",
        current_toolsets=["memory"],
        ceiling_toolsets=["memory", "terminal", "file"],
        disabled_toolsets=["terminal"],
    )

    assert widened == ["file", "memory"]
    assert event is not None
    assert event["added_toolsets"] == ["file"]


def test_recall_toolsets_for_missing_tool_ignores_unsafe_specialist_toolset(monkeypatch):
    monkeypatch.setattr(
        "model_tools.get_toolset_for_tool",
        lambda tool_name: "delegation" if tool_name == "delegate_task" else None,
    )

    widened, event = recall_toolsets_for_missing_tool(
        "delegate_task",
        current_toolsets=["memory"],
        ceiling_toolsets=["delegation", "memory"],
    )

    assert widened == ["memory"]
    assert event is None


def test_widen_agent_tools_for_missing_tool_updates_agent_once_and_records_metric(monkeypatch):
    monkeypatch.setattr(
        "model_tools.get_toolset_for_tool",
        lambda tool_name: "terminal" if tool_name == "terminal" else None,
    )
    monkeypatch.setattr(
        "model_tools.get_tool_definitions",
        lambda enabled_toolsets, disabled_toolsets, quiet_mode: [_tool_schema("terminal")]
        if set(enabled_toolsets) >= {"terminal", "file"}
        else [],
    )
    agent = SimpleNamespace(
        platform="discord",
        enabled_toolsets=["memory"],
        disabled_toolsets=[],
        tools=[],
        valid_tool_names=set(),
        _toolset_recall_ceiling=["memory", "terminal", "file"],
        _toolset_recall_attempts=0,
        _toolset_recall_max_attempts=1,
        _toolset_recall_events=[],
    )

    event = widen_agent_tools_for_missing_tool(agent, "terminal")

    assert event is not None
    assert event["kind"] == "toolset_recall_on_miss"
    assert event["added_toolsets"] == ["file", "terminal"]
    assert agent.enabled_toolsets == ["file", "memory", "terminal"]
    assert agent.valid_tool_names == {"terminal"}
    assert agent._toolset_recall_attempts == 1
    assert agent._toolset_recall_events == [event]


def test_widen_agent_tools_for_missing_tool_is_bounded_to_avoid_retry_loops(monkeypatch):
    monkeypatch.setattr(
        "model_tools.get_toolset_for_tool",
        lambda tool_name: "terminal" if tool_name == "terminal" else None,
    )
    calls = []

    def fake_get_tool_definitions(enabled_toolsets, disabled_toolsets, quiet_mode):
        calls.append(tuple(enabled_toolsets))
        return [_tool_schema("terminal")]

    monkeypatch.setattr("model_tools.get_tool_definitions", fake_get_tool_definitions)
    agent = SimpleNamespace(
        platform="discord",
        enabled_toolsets=["memory"],
        disabled_toolsets=[],
        tools=[],
        valid_tool_names=set(),
        _toolset_recall_ceiling=["memory", "terminal", "file"],
        _toolset_recall_attempts=1,
        _toolset_recall_max_attempts=1,
        _toolset_recall_events=[],
    )

    event = widen_agent_tools_for_missing_tool(agent, "terminal")

    assert event is None
    assert calls == []
    assert agent.enabled_toolsets == ["memory"]
    assert agent.valid_tool_names == set()


def test_widen_agent_tools_for_missing_tool_is_discord_only(monkeypatch):
    monkeypatch.setattr(
        "model_tools.get_toolset_for_tool",
        lambda tool_name: "terminal" if tool_name == "terminal" else None,
    )
    agent = SimpleNamespace(
        platform="cli",
        enabled_toolsets=["memory"],
        disabled_toolsets=[],
        _toolset_recall_ceiling=["memory", "terminal", "file"],
        _toolset_recall_attempts=0,
        _toolset_recall_max_attempts=1,
    )

    assert widen_agent_tools_for_missing_tool(agent, "terminal") is None
