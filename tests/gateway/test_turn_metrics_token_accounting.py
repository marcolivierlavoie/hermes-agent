from types import SimpleNamespace

import asyncio
import time

from gateway.run import (
    _biff_trivial_greeting_fast_response,
    _finalize_gateway_stream_task,
    _gateway_hygiene_needs_compress,
    _gateway_turn_token_metrics,
    _gateway_turn_wall_metrics,
)


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
        wall_time=14.3456,
        gateway_pre_agent_time=0.5,
        gateway_prep_time=1.2345,
        agent_loop_time=10.0,
        gateway_run_agent_overhead_time=1.25,
        gateway_agent_post_loop_time=1.0,
        gateway_agent_token_metrics_time=0.1,
        gateway_agent_media_scan_time=0.2,
        gateway_agent_session_sync_time=0.3,
        gateway_agent_title_dispatch_time=0.4,
        gateway_postprocess_time=1.1111,
        long_turn={
            "elapsed_seconds": 9.8765,
            "tool_calls": 7,
            "checkpoint_count": 2,
            "threshold_reasons": ["api_calls"],
        },
    )

    assert metrics == {
        "wall_time": 14.346,
        "gateway_pre_agent_time": 0.5,
        "gateway_prep_time": 1.234,
        "agent_loop_time": 10.0,
        "gateway_run_agent_overhead_time": 1.25,
        "gateway_agent_post_loop_time": 1.0,
        "gateway_agent_token_metrics_time": 0.1,
        "gateway_agent_media_scan_time": 0.2,
        "gateway_agent_session_sync_time": 0.3,
        "gateway_agent_title_dispatch_time": 0.4,
        "gateway_run_agent_residual_time": 0.25,
        "gateway_postprocess_time": 1.111,
        "gateway_other_time": 0.251,
        "long_turn_elapsed": 9.877,
        "long_turn_tool_calls": 7,
        "long_turn_checkpoints": 2,
        "long_turn_thresholds": "api_calls",
    }


def test_hygiene_skips_hard_message_limit_when_actual_tokens_are_below_threshold():
    needs_compress, reason = _gateway_hygiene_needs_compress(
        approx_tokens=114_566,
        compress_token_threshold=231_200,
        msg_count=465,
        hard_msg_limit=400,
        token_source="actual",
    )

    assert needs_compress is False
    assert reason == "below_threshold"


def test_hygiene_still_compresses_estimated_runaway_message_counts():
    needs_compress, reason = _gateway_hygiene_needs_compress(
        approx_tokens=114_566,
        compress_token_threshold=231_200,
        msg_count=465,
        hard_msg_limit=400,
        token_source="estimated",
    )

    assert needs_compress is True
    assert reason == "hard_message_limit"


def test_hygiene_token_threshold_overrides_actual_token_source():
    needs_compress, reason = _gateway_hygiene_needs_compress(
        approx_tokens=240_000,
        compress_token_threshold=231_200,
        msg_count=100,
        hard_msg_limit=400,
        token_source="actual",
    )

    assert needs_compress is True
    assert reason == "token_threshold"


def test_gateway_stream_task_without_consumer_is_cancelled_without_timeout_wait():
    async def _run():
        async def _idle_poll():
            await asyncio.sleep(30)

        task = asyncio.create_task(_idle_poll())
        started = time.monotonic()
        await _finalize_gateway_stream_task(task, None, timeout=5.0)
        elapsed = time.monotonic() - started

        assert task.cancelled()
        assert elapsed < 0.5

    asyncio.run(_run())


def test_biff_trivial_greeting_fast_path_accepts_exact_discord_salutations():
    assert (
        _biff_trivial_greeting_fast_response(platform="discord", text="hi")
        == "Hi Marco."
    )
    assert (
        _biff_trivial_greeting_fast_response(platform=SimpleNamespace(value="discord"), text="Hello!")
        == "Hi Marco."
    )


def test_biff_trivial_greeting_fast_path_rejects_nontrivial_or_non_discord_messages():
    assert _biff_trivial_greeting_fast_response(platform="slack", text="hi") is None
    assert _biff_trivial_greeting_fast_response(platform="discord", text="hi can you check 637") is None
    assert _biff_trivial_greeting_fast_response(platform="discord", text="check 637") is None
    assert _biff_trivial_greeting_fast_response(platform="discord", text="/help") is None
    assert _biff_trivial_greeting_fast_response(platform="discord", text="<@123> hi") is None
    assert _biff_trivial_greeting_fast_response(platform="discord", text="hi", enabled=False) is None
