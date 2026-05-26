from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.biff_operating_partner import (
    BIFF_OPERATING_PARTNER_MARKER,
    build_biff_operating_partner_guidance,
    build_dreaming_artifact,
    should_enable_biff_operating_partner_guidance,
)
from agent.system_prompt import build_system_prompt_parts


def _minimal_agent() -> SimpleNamespace:
    return SimpleNamespace(
        load_soul_identity=True,
        skip_context_files=True,
        valid_tool_names=[],
        _kanban_worker_guidance="",
        _tool_use_enforcement=False,
        tools=[],
        provider="test",
        model="test-model",
        platform="cli",
        _memory_store=None,
        _memory_enabled=False,
        _user_profile_enabled=False,
        _memory_manager=None,
        pass_session_id=False,
        session_id="test-session",
    )


def test_biff_guidance_enables_for_marco_biff_identity(monkeypatch):
    monkeypatch.delenv("HERMES_BIFF_OPERATING_PARTNER", raising=False)
    identity = "You are Biff, Marco's chief of staff and operator."

    assert should_enable_biff_operating_partner_guidance(identity) is True
    guidance = build_biff_operating_partner_guidance(identity)

    assert BIFF_OPERATING_PARTNER_MARKER in guidance
    for phrase in (
        "Biff Radar",
        "Spark layer",
        "Care layer",
        "Proposal muscles",
        "Idea Shelf / Proposal Queue",
        "Dreaming artifact pipeline",
    ):
        assert phrase in guidance


def test_biff_guidance_does_not_enable_for_generic_hermes(monkeypatch):
    monkeypatch.delenv("HERMES_BIFF_OPERATING_PARTNER", raising=False)

    assert build_biff_operating_partner_guidance("You are Hermes Agent.") == ""


def test_biff_profile_home_enables_guidance_without_default_identity(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_BIFF_OPERATING_PARTNER", raising=False)
    biff_home = tmp_path / "profiles" / "biff"

    assert should_enable_biff_operating_partner_guidance("generic", hermes_home=biff_home) is True


def test_dreaming_artifact_contract_contains_required_lanes():
    artifact = build_dreaming_artifact(
        {
            "practical_improvement": "Make school-night prep visible.",
            "whimsical_idea": "A tiny Friday goblin menu.",
            "family_life_delight": "Leave a soft note for the family.",
            "ai_life_experiment": "Try one assistant-to-assistant handoff ritual.",
            "old_idea_resurrection": "Revive the neglected idea shelf.",
            "automation_candidate": "Draft a weekly friction radar, but do not automate empathy.",
            "biff_noticed_this": "Marco smiles at useful weirdness when it is grounded.",
            "now": ["one concrete change"],
            "later": ["proposal queue item"],
            "idea_shelf": ["tiny whimsical ritual"],
            "do_not_push": ["do not convert family delight into fake work"],
        }
    ).to_markdown()

    for lane in (
        "practical_improvement",
        "whimsical_idea",
        "family_life_delight",
        "ai_life_experiment",
        "old_idea_resurrection",
        "automation_candidate",
        "biff_noticed_this",
        "now",
        "later",
        "shelf",
        "do_not_push",
    ):
        assert f"## {lane}" in artifact
    assert "do not automate empathy" in artifact


def test_system_prompt_injects_operating_partner_overlay_for_biff_identity(monkeypatch):
    monkeypatch.delenv("HERMES_BIFF_OPERATING_PARTNER", raising=False)
    agent = _minimal_agent()
    identity = "You are Biff, Marco's proactive operating partner."

    with patch("run_agent.load_soul_md", return_value=identity), \
         patch("run_agent.build_nous_subscription_prompt", return_value=""), \
         patch("run_agent.build_environment_hints", return_value=""), \
         patch("run_agent.build_context_files_prompt", return_value=""):
        parts = build_system_prompt_parts(agent)

    assert identity in parts["stable"]
    assert BIFF_OPERATING_PARTNER_MARKER in parts["stable"]
    assert "whimsy" in parts["stable"].lower()
    assert "Idea Shelf" in parts["stable"]


def test_system_prompt_leaves_generic_identity_unchanged(monkeypatch):
    monkeypatch.delenv("HERMES_BIFF_OPERATING_PARTNER", raising=False)
    agent = _minimal_agent()

    with patch("run_agent.load_soul_md", return_value="You are Hermes Agent."), \
         patch("run_agent.build_nous_subscription_prompt", return_value=""), \
         patch("run_agent.build_environment_hints", return_value=""), \
         patch("run_agent.build_context_files_prompt", return_value=""):
        parts = build_system_prompt_parts(agent)

    assert BIFF_OPERATING_PARTNER_MARKER not in parts["stable"]
