from types import SimpleNamespace

from gateway.run import _gateway_turn_token_metrics


class _Compressor:
    last_prompt_tokens = 2048
    context_length = 200_000


class _Agent:
    context_compressor = _Compressor()
    # These are session-cumulative counters on cached gateway agents, not
    # single-turn values. turn_metrics must log the current-turn delta.
    session_prompt_tokens = 1_003_000
    session_completion_tokens = 50_400


def test_turn_metrics_token_accounting_uses_cumulative_counter_delta():
    metrics = _gateway_turn_token_metrics(
        _Agent(),
        baseline=SimpleNamespace(input_tokens=1_000_000, output_tokens=50_000),
    )

    assert metrics == {
        "last_prompt_tokens": 2048,
        "input_tokens": 3000,
        "output_tokens": 400,
        "context_length": 200_000,
    }


def test_turn_metrics_token_accounting_falls_back_when_counter_resets():
    agent = SimpleNamespace(
        context_compressor=SimpleNamespace(last_prompt_tokens=512, context_length=8192),
        session_prompt_tokens=250,
        session_completion_tokens=25,
    )

    metrics = _gateway_turn_token_metrics(
        agent,
        baseline=SimpleNamespace(input_tokens=1_000_000, output_tokens=50_000),
    )

    assert metrics["input_tokens"] == 250
    assert metrics["output_tokens"] == 25
    assert metrics["last_prompt_tokens"] == 512
    assert metrics["context_length"] == 8192
