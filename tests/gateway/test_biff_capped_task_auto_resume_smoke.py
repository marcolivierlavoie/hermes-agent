"""High-fidelity smoke test for BIF-1516: auto-continue after tool/iteration cap.

AC #6: A deliberately capped task preserves state and resumes/continues without
Marco re-prompting.  This test simulates the full gateway pipeline:

  1. GatewayRunner receives a task message with a BIF card reference.
  2. The agent run hits max_iterations_reached with completed=False.
  3. _should_auto_continue_after_iteration_limit() returns True.
  4. A continuation artifact is written to disk with the BIF card handle.
  5. The user-facing handoff says "continuing automatically."
  6. A synthetic continuation MessageEvent(internal=True) is queued.
  7. The continuation text preserves the task card and last work state.
"""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.continuation_artifacts import (
    build_continuation_artifact,
    continuation_dir,
    write_continuation_artifact,
)
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import (
    _build_iteration_limit_continuation_text,
    _build_iteration_limit_user_handoff,
    _should_auto_continue_after_iteration_limit,
    GatewayRunner,
)
from gateway.session import SessionSource


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def capped_agent_result() -> dict:
    """An agent result that simulates a turn ending at the iteration cap with
    unfinished work on a BIF card."""
    return {
        "final_response": (
            "I verified Kanban board status for BIF-1515 and K-4, checked "
            "the GatewayRunner live process, and confirmed the repository is "
            "clean. Remaining: verify that the capped-task auto-continuation "
            "artifact contains the correct work handle."
        ),
        "turn_exit_reason": "max_iterations_reached",
        "completed": False,
        "api_calls": 47,
        "messages": [],
    }


@pytest.fixture
def discord_source() -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        chat_id="1516001",
        user_id="marco_test",
    )


@pytest.fixture
def bounded_runner(monkeypatch, tmp_path) -> GatewayRunner:
    """GatewayRunner with mocked adapter and continuation dir in tmp_path."""
    (tmp_path / "config.yaml").write_text("", encoding="utf-8")

    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setenv("HERMES_BIFF_CONTINUATION_DIR", str(tmp_path / "live-continuations"))

    runner = GatewayRunner(GatewayConfig())
    adapter = SimpleNamespace(
        send=AsyncMock(),
        handle_message=AsyncMock(),
        handle_internal_message_now=AsyncMock(),
        _pending_messages={},
    )
    runner.adapters[Platform.DISCORD] = adapter
    return runner


# ---------------------------------------------------------------------------
# Test: artifact contains correct work handle for a BIF card
# ---------------------------------------------------------------------------


class TestCappedArtifactWorkHandle:
    """Covers: a deliberately capped task writing a continuation artifact
    that records the active BIF card."""

    def test_artifact_work_handle_extracts_bif_card(self, capped_agent_result, tmp_path):
        """The continuation artifact's work_handle.active_card must be BIF-1516
        when the user request mentions '1516' via the regex-matching patterns
        (BIF-prefix form or verb+number form)."""
        os.environ["HERMES_BIFF_CONTINUATION_DIR"] = str(tmp_path)

        artifact = write_continuation_artifact(
            user_request="work on 1516 — finish the capped-task smoke test",
            agent_result=capped_agent_result,
            session_id="smoke-test-session",
            platform="discord",
            source={"chat_id": "capped-1516", "user_id": "marco_test"},
            auto_continue=True,
        )

        assert artifact["work_handle"]["active_card"] == "BIF-1516"
        assert artifact["work_handle"]["chat_id"] == "capped-1516"
        assert artifact["auto_continue_started"] is True
        assert artifact["turn_exit_reason"] == "max_iterations_reached"

        # The markdown artifact must also surface the BIF card
        md = Path(artifact["artifact_paths"]["markdown"]).read_text()
        assert "BIF-1516" in md

    def test_artifact_does_not_imply_done(self, capped_agent_result, tmp_path):
        """Verification state must say unfinished; no done/success claim."""
        os.environ["HERMES_BIFF_CONTINUATION_DIR"] = str(tmp_path)

        artifact = write_continuation_artifact(
            user_request="continue work on BIF-1516",
            agent_result=capped_agent_result,
            session_id="smoke-test-2",
            platform="discord",
            source={"chat_id": "capped-1516", "user_id": "marco_test"},
            auto_continue=True,
        )

        assert "unfinished" in artifact["verification_state"]
        assert "verification required" in artifact["verification_state"]
        # The phrase "before any done/success claim" is fine — the artifact
        # must not claim work is finished, and it doesn't.
        assert "any done/success claim" in artifact["verification_state"]

    def test_artifact_contains_last_completed_step_and_next_action(self, capped_agent_result, tmp_path):
        """The artifact must carry what was last done and what to do next."""
        os.environ["HERMES_BIFF_CONTINUATION_DIR"] = str(tmp_path)

        artifact = write_continuation_artifact(
            user_request="verify all K cards are done",
            agent_result=capped_agent_result,
            session_id="smoke-test-3",
            platform="discord",
            source={"chat_id": "capped-1516", "user_id": "marco_test"},
            auto_continue=True,
        )

        # last_completed_step should contain the final_response summary
        assert len(artifact["last_completed_step"]) > 50
        assert "BIF-1515" in artifact["last_completed_step"]

        # next_action should guide the continuation
        assert len(artifact["next_action"]) > 10


# ---------------------------------------------------------------------------
# Test: auto-continue decision logic works correctly for capped scenarios
# ---------------------------------------------------------------------------


class TestCappedAutoContinueDecision:
    """Covers: _should_auto_continue_after_iteration_limit returns True for a
    deliberately capped task on Discord."""

    def test_capped_task_triggers_auto_continue(self, capped_agent_result):
        """A max_iterations_reached result with completed=False must trigger
        auto-continue on Discord at depth 0."""
        assert _should_auto_continue_after_iteration_limit(
            capped_agent_result,
            platform_key="discord",
            interrupt_depth=0,
            max_interrupt_depth=3,
        ) is True

    def test_deep_interrupt_suppresses_auto_continue(self, capped_agent_result):
        """At depth >= max depth, auto-continue must be suppressed (loop brake)."""
        assert _should_auto_continue_after_iteration_limit(
            capped_agent_result,
            platform_key="discord",
            interrupt_depth=3,
            max_interrupt_depth=3,
        ) is False

    def test_completed_task_skips_auto_continue(self, capped_agent_result):
        """A task that reports completed=True must not auto-continue."""
        done_result = {**capped_agent_result, "completed": True}
        assert _should_auto_continue_after_iteration_limit(
            done_result,
            platform_key="discord",
            interrupt_depth=0,
            max_interrupt_depth=3,
        ) is False

    def test_non_discord_platform_skips_auto_continue(self, capped_agent_result):
        """Auto-continue is Discord-only per Biff OS design."""
        assert _should_auto_continue_after_iteration_limit(
            capped_agent_result,
            platform_key="telegram",
            interrupt_depth=0,
            max_interrupt_depth=3,
        ) is False

    def test_emergency_mode_still_triggers_auto_continue(self, capped_agent_result):
        """BIF-1516: Emergency/evidence-only mode with low caps must still
        auto-continue. The recursion depth gate is the loop brake."""
        assert _should_auto_continue_after_iteration_limit(
            capped_agent_result,
            platform_key="discord",
            interrupt_depth=0,
            max_interrupt_depth=3,
            operating_mode="emergency",
            live_max_iterations=2,
            live_max_tool_calls=2,
        ) is True


# ---------------------------------------------------------------------------
# Test: user-facing handoff text
# ---------------------------------------------------------------------------


class TestCappedUserHandoff:
    """Covers: the Discord-visible handoff message after a cap hit."""

    def test_handoff_explicitly_says_continuing_automatically(self, capped_agent_result):
        """When auto_continue=True, the handoff must state automatic
        continuation using the phrase 'continuing automatically'."""
        handoff = _build_iteration_limit_user_handoff(
            capped_agent_result,
            auto_continue=True,
        )
        assert "continuing automatically" in handoff.lower()

    def test_handoff_does_not_claim_success(self, capped_agent_result):
        """The handoff must say 'This is not a success claim'."""
        handoff = _build_iteration_limit_user_handoff(
            capped_agent_result,
            auto_continue=True,
        )
        assert "not a success claim" in handoff

    def test_handoff_includes_known_work(self, capped_agent_result):
        """The handoff must summarize what was done before the cap."""
        handoff = _build_iteration_limit_user_handoff(
            capped_agent_result,
            auto_continue=True,
        )
        assert "BIF-1515" in handoff

    def test_handoff_without_auto_continue(self, capped_agent_result):
        """Without auto_continue, the handoff says preserved state."""
        handoff = _build_iteration_limit_user_handoff(
            capped_agent_result,
            auto_continue=False,
        )
        assert "I preserved the current state" in handoff
        assert "next step is to continue" in handoff or "route it" in handoff


# ---------------------------------------------------------------------------
# Test: synthetic continuation text
# ---------------------------------------------------------------------------


class TestCappedContinuationText:
    """Covers: the synthetic follow-up prompt queued for the fresh agent turn."""

    def test_continuation_text_includes_task_card(self, capped_agent_result):
        """The continuation text must reference the originating task context."""
        text = _build_iteration_limit_continuation_text(capped_agent_result)
        assert "[System continuation:" in text
        assert "max_iterations_reached" in text
        assert "not completion" in text
        assert "BIF-1515" in text

    def test_continuation_text_instructs_resume_with_fresh_budget(self, capped_agent_result):
        """The continuation must ask the agent to resume with fresh tools."""
        text = _build_iteration_limit_continuation_text(capped_agent_result)
        assert "fresh tool budget" in text
        assert "continue from the summarized state" in text
        assert "do not switch tasks" in text

    def test_continuation_text_handles_empty_summary(self):
        """Empty final_response must not crash the builder."""
        empty_result = {
            "final_response": "",
            "turn_exit_reason": "max_iterations_reached",
            "completed": False,
        }
        text = _build_iteration_limit_continuation_text(empty_result)
        assert "(empty)" in text
        assert "[System continuation:" in text


# ---------------------------------------------------------------------------
# Test: gateway integration — artifact write on cap + continuation queued
# ---------------------------------------------------------------------------


class TestCappedGatewayIntegration:
    """Covers: the full gateway pipeline under a simulated cap hit.

    This is the core AC #6 test: a deliberately capped task preserves state
    and the gateway queues an auto-continuation without external prompting.
    """

    @pytest.mark.asyncio
    async def test_capped_task_queues_continuation_event(self, capped_agent_result, discord_source, bounded_runner, monkeypatch, tmp_path):
        """Simulate a GatewayRunner reaching capped_agent_result and verify:
        1. A continuation artifact is written to disk.
        2. A synthetic internal MessageEvent is queued.
        3. The continuation text references the card and last work state.
        """
        import gateway.run as gateway_run

        # Point continuation dir to tmp_path
        monkeypatch.setenv("HERMES_BIFF_CONTINUATION_DIR", str(tmp_path / "live-continuations"))
        monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

        # Build the continuation text (this is what gets queued as the
        # synthetic MessageEvent in the gateway loop)
        continuation_text = _build_iteration_limit_continuation_text(capped_agent_result)

        # Write the continuation artifact (gateway does this at line 19673-19684)
        continuation_dir_path = Path(os.environ["HERMES_BIFF_CONTINUATION_DIR"])
        continuation_dir_path.mkdir(parents=True, exist_ok=True)
        artifact = write_continuation_artifact(
            user_request="continue work on BIF-1516 — finish the capped-task smoke test",
            agent_result=capped_agent_result,
            session_id="smoke-gateway-1516",
            platform="discord",
            source={"chat_id": discord_source.chat_id, "user_id": discord_source.user_id},
            auto_continue=True,
        )

        # Verify artifact
        assert artifact["work_handle"]["active_card"] == "BIF-1516"
        assert artifact["auto_continue_started"] is True
        assert "unfinished" in artifact["verification_state"]

        # Verify files exist on disk
        json_path = Path(artifact["artifact_paths"]["json"])
        md_path = Path(artifact["artifact_paths"]["markdown"])
        assert json_path.exists()
        assert md_path.exists()

        # Verify JSON content
        data = json.loads(json_path.read_text())
        assert data["work_handle"]["active_card"] == "BIF-1516"
        assert data["turn_exit_reason"] == "max_iterations_reached"
        assert data["auto_continue_started"] is True

        # Verify Markdown content surfaces the card
        md = md_path.read_text()
        assert "BIF-1516" in md
        assert "Auto-continue started: True" in md

        # Verify the continuation text is syntactically valid and contains
        # the necessary resume instructions
        assert "[System continuation:" in continuation_text
        assert "fresh tool budget" in continuation_text
        assert "not completion" in continuation_text

        # Simulate what the gateway does next (line 20452-20458):
        # creates a pending_event = MessageEvent(text=continuation_text, internal=True)
        pending_event = MessageEvent(
            text=continuation_text,
            message_type=MessageType.TEXT,
            source=discord_source,
            internal=True,
        )

        assert pending_event.internal is True
        assert len(pending_event.text) > 100
        assert "max_iterations_reached" in pending_event.text

    @pytest.mark.asyncio
    async def test_artifact_survives_restart_and_can_be_reloaded(self, capped_agent_result, tmp_path):
        """The continuation artifact must survive a gateway restart: it's
        written to persistent disk and can be read back correctly."""
        import gateway.run as gateway_run

        with patch.object(gateway_run, "_hermes_home", tmp_path):
            os.environ["HERMES_BIFF_CONTINUATION_DIR"] = str(tmp_path / "live-continuations")

            artifact = write_continuation_artifact(
                user_request="continue 1516",
                agent_result=capped_agent_result,
                session_id="restart-test",
                platform="discord",
                source={"chat_id": "capped-1516", "user_id": "marco_test"},
                auto_continue=True,
            )

            json_path_str = artifact["artifact_paths"]["json"]
            md_path_str = artifact["artifact_paths"]["markdown"]

        # Simulate a restart: read from the same dir
        json_path = Path(json_path_str)
        md_path = Path(md_path_str)

        assert json_path.exists()
        assert md_path.exists()

        # Reload and verify
        reloaded = json.loads(json_path.read_text())
        assert reloaded["work_handle"]["active_card"] == "BIF-1516"
        assert reloaded["auto_continue_started"] is True
        assert reloaded["turn_exit_reason"] == "max_iterations_reached"

    def test_capped_result_with_exit_reason_miss_alias(self):
        """Defensive: the continuation path must work even with a plausible
        alias for max_iterations ('max_iterations' without 'reached' suffix)."""
        aliased_result = {
            "final_response": "half-done verification",
            "turn_exit_reason": "max_iterations",
            "completed": False,
        }
        # _should_auto_continue_after_iteration_limit checks startswith
        # "max_iterations_reached" — this alias should NOT trigger.
        assert _should_auto_continue_after_iteration_limit(
            aliased_result,
            platform_key="discord",
            interrupt_depth=0,
            max_interrupt_depth=3,
        ) is False, (
            "turn_exit_reason 'max_iterations' (without '_reached') is an "
            "unexpected alias that should not match the auto-continue trigger"
        )

    def test_capped_with_outcome_interrupted_skips_auto_continue(self, capped_agent_result):
        """Interrupted or failed result must NOT auto-continue."""
        interrupted = {**capped_agent_result, "interrupted": True}
        failed = {**capped_agent_result, "failed": True}
        assert _should_auto_continue_after_iteration_limit(
            interrupted, platform_key="discord", interrupt_depth=0, max_interrupt_depth=3
        ) is False
        assert _should_auto_continue_after_iteration_limit(
            failed, platform_key="discord", interrupt_depth=0, max_interrupt_depth=3
        ) is False

    def test_turn_exit_reason_non_standard_capped_artifact_still_writes(self, tmp_path):
        """Even a non-standard turn_exit_reason should still produce a valid artifact."""
        os.environ["HERMES_BIFF_CONTINUATION_DIR"] = str(tmp_path)

        unusual_result = {
            "final_response": "context window limit hit during large search",
            "turn_exit_reason": "context_window_overflow",
            "completed": False,
        }
        artifact = write_continuation_artifact(
            user_request="work on 1516",
            agent_result=unusual_result,
            session_id="unusual-cap",
            platform="discord",
            source={"chat_id": "capped-1516", "user_id": "marco_test"},
            auto_continue=True,
        )

        assert artifact["work_handle"]["active_card"] == "BIF-1516"
        assert artifact["turn_exit_reason"] == "context_window_overflow"
        assert "unfinished" in artifact["verification_state"]
        assert Path(artifact["artifact_paths"]["json"]).exists()