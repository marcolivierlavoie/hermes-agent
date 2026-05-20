from __future__ import annotations

import importlib.util
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FLOW_PATH = ROOT.parent / "plugins" / "daily_health_flow" / "flow.py"
STARTER_PATH = ROOT.parent / "scripts" / "start-daily-health-flow.py"


def load_flow_module():
    spec = importlib.util.spec_from_file_location("daily_health_flow_test_flow", FLOW_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_starter_module():
    spec = importlib.util.spec_from_file_location("daily_health_flow_test_starter", STARTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_command_bypass_catches_common_biff_issue_work_phrases():
    flow = load_flow_module()

    examples = {
        "Continue 616": "leading_operation_command",
        "continue BIF-616": "leading_operation_command",
        "Well you need to run the health flow now to test": "assistant_request_prefix",
        "what’s the status of BIF-616": "status_question",
        "please verify BIF-616": "polite_operation_command",
    }

    for text, expected_reason in examples.items():
        assert flow.command_intent_reason(text) == expected_reason


def test_plain_ritual_answers_still_do_not_bypass():
    flow = load_flow_module()

    answers = [
        "I can choose one small next step",
        "mind-reading",
        "My mind jumps to the worst outcome",
    ]

    for text in answers:
        assert flow.command_intent_reason(text) is None


def test_starter_dry_run_is_link_only_and_source_scoped(capsys):
    starter = load_starter_module()

    assert starter.main(["--dry-run"]) == 0

    output = capsys.readouterr().out
    assert "daily_health_flow_reminder_ready" in output
    assert "source=n8n_discord_starter" in output
    assert "ritual_url=https://biff.tail460c2.ts.net/ritual" in output
    assert "message_type=link_only" in output
    assert "starts_capture_state=false" in output
    assert "deactivates_existing_state=true" in output
    assert "What is one part I directly control" not in output
    assert "question" not in output.lower()


def test_starter_sends_only_ritual_link_and_stops_existing_state(monkeypatch, capsys):
    starter = load_starter_module()
    stopped = []
    sent = []

    fake_flow = types.SimpleNamespace(
        stop_flow=lambda *, reason: stopped.append(reason) or {"active": False},
        start_flow=lambda **_: (_ for _ in ()).throw(AssertionError("must not start capture state")),
        set_last_question_message_id=lambda *_: (_ for _ in ()).throw(AssertionError("must not track Discord question")),
        question_message=lambda *_: (_ for _ in ()).throw(AssertionError("must not render Q1")),
    )
    monkeypatch.setattr(starter, "load_flow_module", lambda: fake_flow)
    monkeypatch.setattr(starter, "send_discord_message", lambda content: sent.append(content) or "msg-1")

    assert starter.main([]) == 0

    assert stopped == ["ritual_link_reminder"]
    assert sent == [starter.REMINDER_MESSAGE]
    assert "https://biff.tail460c2.ts.net/ritual" in sent[0]
    assert "What is one part I directly control" not in sent[0]
    assert "Reply in this channel" not in sent[0]
    output = capsys.readouterr().out
    assert "daily_health_flow_reminder_sent" in output
    assert "source=n8n_discord_starter" in output
    assert "starts_capture_state=false" in output
    assert "deactivated_existing_state=true" in output
