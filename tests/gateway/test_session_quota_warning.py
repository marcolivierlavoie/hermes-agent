import asyncio

import pytest

from gateway.run import _apply_final_turn_trailing_lines, _final_turn_trailing_metadata
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, GatewayResponse, MessageEvent, SendResult
from gateway.session import SessionSource, build_session_key
from gateway.session_hygiene import (
    build_session_quota_recommendation,
    session_quota_threshold_for_tokens,
)


def test_session_quota_thresholds_and_messages():
    below = build_session_quota_recommendation(
        session_id="s1",
        prompt_tokens=39_999,
        context_length=200_000,
    )
    assert below is None

    heads_up = build_session_quota_recommendation(
        session_id="s1",
        prompt_tokens=40_000,
        context_length=200_000,
    )
    assert heads_up is not None
    assert heads_up.threshold == 40_000
    assert heads_up.level == "heads_up"
    assert heads_up.dedupe_key == "session-quota:s1:40000"
    assert "40,000" in heads_up.text
    assert "keep going" in heads_up.text.lower()
    assert "auto" not in heads_up.text.lower()

    strong = build_session_quota_recommendation(
        session_id="s1",
        prompt_tokens=100_001,
        context_length=150_000,
    )
    assert strong is not None
    assert strong.threshold == 100_000
    assert strong.level == "strong_economy"
    assert "economy" in strong.text.lower()
    assert "reset" in strong.text.lower()


def test_session_quota_returns_highest_crossed_threshold():
    assert session_quota_threshold_for_tokens(70_000) == 70_000
    assert session_quota_threshold_for_tokens(99_999) == 70_000
    assert session_quota_threshold_for_tokens(130_000) == 130_000


def test_session_quota_warned_threshold_dedupe():
    rec = build_session_quota_recommendation(
        session_id="s1",
        prompt_tokens=100_000,
        warned_thresholds=[40_000, 70_000, 100_000],
    )
    assert rec is None

    rec = build_session_quota_recommendation(
        session_id="s1",
        prompt_tokens=130_000,
        warned_thresholds=[40_000, 70_000, 100_000],
    )
    assert rec is not None
    assert rec.threshold == 130_000
    assert rec.dedupe_key == "session-quota:s1:130000"


def test_quota_note_appends_only_to_non_streaming_final_response():
    response, trailing_lines, quota_delivered = _apply_final_turn_trailing_lines(
        "final answer",
        quota_line="⚠️ quota note",
        footer_line="model · 50%",
        already_sent=False,
    )

    assert response == "final answer\n\n⚠️ quota note\nmodel · 50%"
    assert trailing_lines == []
    assert quota_delivered is True


def test_streaming_quota_turn_returns_trailing_send_and_persistence_marker():
    response, trailing_lines, quota_delivered = _apply_final_turn_trailing_lines(
        "streamed body already delivered",
        quota_line="⚠️ quota note",
        footer_line="",
        already_sent=True,
    )

    assert response == "streamed body already delivered"
    # Streaming final answers already sent the body; quota warnings are emitted
    # as a separate trailing platform message so Discord can attach the
    # quota-specific New session button only to the warning.
    assert trailing_lines == ["⚠️ quota note"]
    assert quota_delivered is True


def test_streaming_quota_marked_warned_after_trailing_warning_delivery():
    rec = build_session_quota_recommendation(
        session_id="s1",
        prompt_tokens=40_000,
        warned_thresholds=[],
    )
    assert rec is not None
    response, trailing_lines, delivered = _apply_final_turn_trailing_lines(
        "streamed",
        quota_line=f"⚠️ {rec.text}",
        already_sent=True,
    )
    assert response == "streamed"
    assert trailing_lines == [f"⚠️ {rec.text}"]
    assert delivered is True


def test_discord_new_session_button_metadata_is_quota_warning_only():
    base = {"thread_id": "t1"}

    assert _final_turn_trailing_metadata(
        base,
        platform=Platform.DISCORD,
        quota_threshold_to_persist=None,
    ) == {"thread_id": "t1"}

    assert _final_turn_trailing_metadata(
        base,
        platform=Platform.DISCORD,
        quota_threshold_to_persist=40_000,
    ) == {"thread_id": "t1", "discord_new_session_button": True}

    assert _final_turn_trailing_metadata(
        base,
        platform=Platform.TELEGRAM,
        quota_threshold_to_persist=40_000,
    ) == {"thread_id": "t1"}


class _QuotaMetadataAdapter(BasePlatformAdapter):
    platform = Platform.DISCORD

    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="test"), Platform.DISCORD)
        self.sent = []

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def send(self, chat_id, content, **kwargs):
        return SendResult(success=True, message_id="send-1")

    async def get_chat_info(self, chat_id):
        return {}

    async def send_typing(self, chat_id, **kwargs):
        return None

    async def _keep_typing(self, chat_id, *args, **kwargs):
        await asyncio.sleep(999)

    async def _send_with_retry(self, **kwargs):
        self.sent.append(kwargs)
        return SendResult(success=True, message_id="msg-1")


def _quota_metadata_event() -> MessageEvent:
    return MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.DISCORD,
            user_id="u1",
            chat_id="c1",
            user_name="tester",
            chat_type="dm",
        ),
        message_id="m1",
    )


async def _run_base_final_send(response: str | GatewayResponse) -> dict:
    adapter = _QuotaMetadataAdapter()

    async def handler(_event):
        return response

    adapter.set_message_handler(handler)
    event = _quota_metadata_event()
    session_key = build_session_key(event.source)
    adapter._active_sessions[session_key] = asyncio.Event()
    await adapter._process_message_background(event, session_key)
    assert adapter.sent
    return adapter.sent[-1].get("metadata") or {}


@pytest.mark.asyncio
async def test_non_streaming_normal_final_reply_has_no_new_session_button_metadata():
    metadata = await _run_base_final_send("normal final answer")

    assert metadata.get("notify") is True
    assert "discord_new_session_button" not in metadata


@pytest.mark.asyncio
async def test_non_streaming_normal_final_reply_mentioning_session_quota_has_no_button():
    metadata = await _run_base_final_send(
        "Normal answer discussing the words Session quota in documentation, not a warning."
    )

    assert metadata.get("notify") is True
    assert "discord_new_session_button" not in metadata


@pytest.mark.asyncio
async def test_non_streaming_quota_warning_final_reply_has_new_session_button_metadata():
    metadata = await _run_base_final_send(
        GatewayResponse(
            "normal final answer\n\n⚠️ Session quota heads up (40,000 prompt tokens): reset soon",
            metadata={"discord_new_session_button": True},
        )
    )

    assert metadata.get("notify") is True
    assert metadata.get("discord_new_session_button") is True
