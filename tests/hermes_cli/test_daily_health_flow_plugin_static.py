from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FLOW_PATH = ROOT.parent / "plugins" / "daily_health_flow" / "flow.py"


def load_flow_module():
    spec = importlib.util.spec_from_file_location("daily_health_flow_test_flow", FLOW_PATH)
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
