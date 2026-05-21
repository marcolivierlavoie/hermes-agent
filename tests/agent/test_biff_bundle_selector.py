import json
from pathlib import Path

import pytest

from agent.biff_bundle_selector import (
    select_biff_bundle_for_prompt,
    should_attempt_biff_bundle_auto_selection,
)


_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "biff_bundle_selector" / "bundles.json"


@pytest.fixture()
def bundles():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (
            "Implement BIF-628 in the Hermes gateway runtime with focused tests and fixtures.",
            "/biff-hermes-runtime-change",
        ),
        (
            "Continue BIF-512 and close the Linear issue after Vex verifies acceptance.",
            "/biff-issue-execution",
        ),
        (
            "Decide whether this should be an n8n webhook, watcher, MCP server, or Hermes cron.",
            "/biff-automation-ownership",
        ),
        (
            "Synthesize this YouTube transcript into a decision brief with evidence and recommendation.",
            "/biff-research-to-decision",
        ),
        (
            "Prep follow-up from the Teams meeting and draft the email action items.",
            "/biff-meeting-followup",
        ),
        (
            "Should this correction go to Mnemosyne memory, Obsidian, Linear, or nowhere?",
            "/biff-memory-knowledge-governance",
        ),
        (
            "Add a reminder and update the grocery shopping list for this weekend.",
            "/biff-personal-logistics",
        ),
    ],
)
def test_selects_existing_biff_bundle_for_natural_language_prompts(bundles, prompt, expected):
    selection = select_biff_bundle_for_prompt(prompt, bundles)

    assert selection is not None
    assert selection.command_key == expected
    assert selection.score >= 4


def test_ignores_non_biff_bundles(bundles):
    selection = select_biff_bundle_for_prompt("Run the not-biff workflow", bundles)

    assert selection is None


def test_returns_none_for_low_signal_prompt(bundles):
    selection = select_biff_bundle_for_prompt("Hey Biff, quick question for you", bundles)

    assert selection is None


def test_explicit_slash_commands_take_precedence_over_auto_selection(bundles):
    assert not should_attempt_biff_bundle_auto_selection("biff-hermes-runtime-change")
    assert not should_attempt_biff_bundle_auto_selection("status")
    assert should_attempt_biff_bundle_auto_selection(None)
    assert should_attempt_biff_bundle_auto_selection("")

    # If a caller ignored the guard, the selector could match the text after a
    # slash command. Gateway/run.py applies the guard before calling it.
    selection = select_biff_bundle_for_prompt(
        "/biff-issue-execution Implement BIF-628 in the Hermes gateway.",
        bundles,
    )
    assert selection is not None


def test_only_selects_bundles_that_exist(bundles):
    bundles.pop("/biff-hermes-runtime-change")

    selection = select_biff_bundle_for_prompt(
        "Patch the Hermes gateway runtime and add focused tests.",
        bundles,
    )

    assert selection is None
