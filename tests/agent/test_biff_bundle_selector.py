import json
from pathlib import Path

import pytest

from agent.biff_bundle_selector import (
    is_direct_question_without_action,
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
            "Continue BIF-512 and close the legacy tracker issue after Vex verifies acceptance.",
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


def test_direct_questions_stay_on_lean_chat_path(bundles):
    assert is_direct_question_without_action("Should this correction go to Mnemosyne memory or Obsidian?")

    selection = select_biff_bundle_for_prompt(
        "Should this correction go to Mnemosyne memory, Obsidian, legacy tracker, or nowhere?",
        bundles,
    )

    assert selection is None


def test_action_questions_can_still_select_bundle(bundles):
    assert not is_direct_question_without_action("Can you check Mnemosyne and update Obsidian if needed?")

    selection = select_biff_bundle_for_prompt(
        "Can you check Mnemosyne and update Obsidian if needed?",
        bundles,
    )

    assert selection is not None
    assert selection.command_key == "/biff-memory-knowledge-governance"


def test_follow_up_work_requests_select_issue_execution_bundle(bundles):
    for prompt in (
        "Continue",
        "Do what you have to do",
        "Now continue and don't stop until done",
        "COMPLETE 1306",
        "finish 1306. Dont stop until you're done.",
        "We'll create the card, document it and give me the number",
    ):
        selection = select_biff_bundle_for_prompt(prompt, bundles)

        assert selection is not None
        assert selection.command_key == "/biff-issue-execution"


def test_explicit_ranger_task_correction_selects_issue_execution_bundle(bundles):
    selection = select_biff_bundle_for_prompt("this is a task for ranger, not forge", bundles)

    assert selection is not None
    assert selection.command_key == "/biff-issue-execution"


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
