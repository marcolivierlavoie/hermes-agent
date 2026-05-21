from types import SimpleNamespace

from gateway.run import _gateway_turn_token_metrics, _gateway_turn_wall_metrics


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


def test_turn_wall_metrics_reports_phase_durations_and_long_turn_counts():
    metrics = _gateway_turn_wall_metrics(
        wall_time=12.3456,
        gateway_prep_time=1.2345,
        agent_loop_time=10.0,
        gateway_postprocess_time=1.1111,
        long_turn={
            "elapsed_seconds": 9.8765,
            "tool_calls": 7,
            "checkpoint_count": 2,
            "threshold_reasons": ["api_calls"],
        },
    )

    assert metrics == {
        "wall_time": 12.346,
        "gateway_prep_time": 1.234,
        "agent_loop_time": 10.0,
        "gateway_postprocess_time": 1.111,
        "gateway_other_time": 0.001,
        "long_turn_elapsed": 9.877,
        "long_turn_tool_calls": 7,
        "long_turn_checkpoints": 2,
        "long_turn_thresholds": "api_calls",
    }
