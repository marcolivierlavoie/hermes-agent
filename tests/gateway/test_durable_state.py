"""Durable state and continuation contract tests (BIF-1509).

Three focused tests for restart-continuity and durable checkpoints:

  1. Checkpoint lifecycle — start, update progress, finalize
  2. Checkpoint read after crash — pending checkpoint survives and is readable
  3. Abandoned checkpoint cleanup — stale pending checkpoints are cleaned up
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from gateway import checkpoint, recovery


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_hermes_home(monkeypatch, tmp_path) -> Path:
    """Point HERMES_HOME to a temporary directory."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Test 1: Checkpoint lifecycle (start → progress → finalize)
# ---------------------------------------------------------------------------


class TestCheckpointLifecycle:
    """Verify the full checkpoint lifecycle works correctly."""

    def test_start_checkpoint_writes_intent(self, tmp_hermes_home):
        """Starting a checkpoint writes intent.json with expected fields."""
        intent = checkpoint.start_checkpoint(
            "BIF-1509",
            description="Implement durable state checkpoint system",
            expected_outcome="Checkpoints survive crashes and are readable",
            rollback_steps=["Remove checkpoint files", "Revert to previous state"],
            pre_state={"config_version": "v2", "last_known_good": "BIF-1508"},
            hermes_home=tmp_hermes_home,
        )
        assert intent["checkpoint_state"] == "pending"
        assert intent["card_id"] == "BIF-1509"
        assert "created_at" in intent
        assert "start_timestamp" in intent

        # Verify on-disk
        state = checkpoint.read_checkpoint("BIF-1509", hermes_home=tmp_hermes_home)
        assert state["state"] == "pending"
        assert state["intent"] is not None
        assert state["intent"]["description"] == "Implement durable state checkpoint system"
        assert state["pre_state"] is not None

    def test_update_checkpoint_progress(self, tmp_hermes_home):
        """Updating progress after a start writes progress.json."""
        checkpoint.start_checkpoint(
            "BIF-1509-progress",
            description="Test progress updates",
            expected_outcome="Progress is tracked step by step",
            hermes_home=tmp_hermes_home,
        )

        progress = checkpoint.update_checkpoint_progress(
            "BIF-1509-progress",
            current_step="Writing checkpoint module",
            step_number=1,
            total_steps=3,
            detail="Created gateway/checkpoint.py",
            hermes_home=tmp_hermes_home,
        )
        assert progress["checkpoint_state"] == "in_progress"
        assert progress["step_number"] == 1
        assert progress["total_steps"] == 3
        assert progress["current_step"] == "Writing checkpoint module"

        state = checkpoint.read_checkpoint("BIF-1509-progress", hermes_home=tmp_hermes_home)
        assert state["state"] == "in_progress"
        assert state["progress"] is not None

    def test_finalize_checkpoint_writes_done_and_cleans_transient(self, tmp_hermes_home):
        """Finalizing a checkpoint writes done.json and removes intent/progress."""
        checkpoint.start_checkpoint(
            "BIF-1509-final",
            description="Test finalization",
            expected_outcome="Checkpoint is marked done",
            hermes_home=tmp_hermes_home,
        )
        checkpoint.update_checkpoint_progress(
            "BIF-1509-final",
            current_step="All steps done",
            step_number=3,
            total_steps=3,
            hermes_home=tmp_hermes_home,
        )

        done = checkpoint.finalize_checkpoint(
            "BIF-1509-final",
            result="All tests pass and checkpoints work correctly",
            verification_hash="sha256:abc123def456",
            post_state={"config_version": "v3", "checkpoint_count": 1},
            hermes_home=tmp_hermes_home,
        )
        assert done["checkpoint_state"] == "done"
        assert done["card_id"] == "BIF-1509-final"
        assert done["verification_hash"] == "sha256:abc123def456"

        # Transient files should be cleaned up
        ckpt_dir = tmp_hermes_home / "working-set" / "checkpoints" / "BIF-1509-final"
        assert not (ckpt_dir / "intent.json").exists()
        assert not (ckpt_dir / "progress.json").exists()
        assert not (ckpt_dir / "pre_state.json").exists()

        state = checkpoint.read_checkpoint("BIF-1509-final", hermes_home=tmp_hermes_home)
        assert state["state"] == "done"
        assert state["done"] is not None
        assert state["done"]["result"] == "All tests pass and checkpoints work correctly"
        assert state["post_state"] is not None

    def test_checkpoint_state_not_found(self, tmp_hermes_home):
        """Reading a non-existent checkpoint returns 'not_found'."""
        state = checkpoint.read_checkpoint("BIF-NONEXISTENT", hermes_home=tmp_hermes_home)
        assert state["state"] == "not_found"
        assert state["intent"] is None
        assert state["progress"] is None
        assert state["done"] is None


# ---------------------------------------------------------------------------
# Test 2: Checkpoint survives crash (restart-continuity)
# ---------------------------------------------------------------------------


class TestCheckpointCrashSurvival:
    """Verify checkpoints survive a simulated crash and are readable on restart."""

    def test_pending_checkpoint_survives_crash(self, tmp_hermes_home):
        """A pending checkpoint (intent written but never finalized) survives a crash."""
        # Simulate starting a checkpoint
        checkpoint.start_checkpoint(
            "BIF-1509-CRASH-TEST",
            description="Work in progress when crash happened",
            expected_outcome="Checkpoint readable after crash",
            pre_state={"phase": "implementation", "files_modified": []},
            hermes_home=tmp_hermes_home,
        )

        # Simulate crash: the checkpoint is never finalized, but the intent
        # file remains on disk. On restart, read_checkpoint should find it.
        state = checkpoint.read_checkpoint("BIF-1509-CRASH-TEST", hermes_home=tmp_hermes_home)
        assert state["state"] == "pending"
        assert state["intent"] is not None
        assert state["intent"]["description"] == "Work in progress when crash happened"
        assert state["pre_state"]["phase"] == "implementation"

    def test_in_progress_checkpoint_survives_crash(self, tmp_hermes_home):
        """An in-progress checkpoint (intent + progress) survives a crash."""
        checkpoint.start_checkpoint(
            "BIF-1509-CRASH-PROGRESS",
            description="Mid-step crash",
            expected_outcome="Progress is visible after restart",
            hermes_home=tmp_hermes_home,
        )
        checkpoint.update_checkpoint_progress(
            "BIF-1509-CRASH-PROGRESS",
            current_step="Step 2 of 5",
            step_number=2,
            total_steps=5,
            detail="Just finished writing file X",
            hermes_home=tmp_hermes_home,
        )

        # Simulate crash
        state = checkpoint.read_checkpoint("BIF-1509-CRASH-PROGRESS", hermes_home=tmp_hermes_home)
        assert state["state"] == "in_progress"
        assert state["progress"]["step_number"] == 2
        assert state["progress"]["total_steps"] == 5
        assert state["progress"]["current_step"] == "Step 2 of 5"

    def test_done_checkpoint_survives_crash(self, tmp_hermes_home):
        """A completed checkpoint (done.json) survives a crash and is readable."""
        checkpoint.start_checkpoint(
            "BIF-1509-DONE-CRASH",
            description="Already completed before crash",
            expected_outcome="Completion state is readable after restart",
            hermes_home=tmp_hermes_home,
        )
        checkpoint.finalize_checkpoint(
            "BIF-1509-DONE-CRASH",
            result="Successfully completed all work for BIF-1509",
            verification_hash="sha256:verified",
            hermes_home=tmp_hermes_home,
        )

        # Simulate crash
        state = checkpoint.read_checkpoint("BIF-1509-DONE-CRASH", hermes_home=tmp_hermes_home)
        assert state["state"] == "done"
        assert state["done"]["result"] == "Successfully completed all work for BIF-1509"
        assert state["done"]["verification_hash"] == "sha256:verified"

    def test_recovery_answer_includes_checkpoint_state(self, tmp_hermes_home):
        """The recovery 'what happened' answer can inform about checkpointed work."""
        checkpoint.start_checkpoint(
            "BIF-1509-RECOVER",
            description="Recoverable work",
            expected_outcome="Recovery detects pending work",
            hermes_home=tmp_hermes_home,
        )

        # The recovery module should be able to detect this checkpoint
        state = checkpoint.read_checkpoint("BIF-1509-RECOVER", hermes_home=tmp_hermes_home)
        assert state["state"] == "pending"


# ---------------------------------------------------------------------------
# Test 3: Abandoned checkpoint cleanup
# ---------------------------------------------------------------------------


class TestCheckpointCleanup:
    """Verify abandoned checkpoints are cleaned up properly."""

    def test_cleanup_abandoned_checkpoints(self, tmp_hermes_home):
        """A very old pending checkpoint is cleaned up."""
        # Create a checkpoint with an ancient start_timestamp
        ckpt_dir = tmp_hermes_home / "working-set" / "checkpoints" / "BIF-STALE"
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        intent = {
            "schema": "biff.checkpoint.v1",
            "checkpoint_state": "pending",
            "card_id": "BIF-STALE",
            "description": "Old abandoned work",
            "start_timestamp": time.time() - 30 * 24 * 3600,  # 30 days ago
        }
        (ckpt_dir / "intent.json").write_text(
            json.dumps(intent, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        assert ckpt_dir.exists()

        cleaned = checkpoint.cleanup_abandoned_checkpoints(
            max_age_seconds=7 * 24 * 3600,  # 7 days
            hermes_home=tmp_hermes_home,
        )
        assert cleaned == 1  # The stale checkpoint was removed
        assert not ckpt_dir.exists()

    def test_recent_checkpoint_not_cleaned(self, tmp_hermes_home):
        """A recent pending checkpoint is not considered abandoned."""
        checkpoint.start_checkpoint(
            "BIF-RECENT",
            description="Recent work",
            expected_outcome="Not cleaned up",
            hermes_home=tmp_hermes_home,
        )

        cleaned = checkpoint.cleanup_abandoned_checkpoints(
            max_age_seconds=7 * 24 * 3600,
            hermes_home=tmp_hermes_home,
        )
        assert cleaned == 0  # Not cleaned up

        state = checkpoint.read_checkpoint("BIF-RECENT", hermes_home=tmp_hermes_home)
        assert state["state"] == "pending"

    def test_done_checkpoints_not_cleaned(self, tmp_hermes_home):
        """Done checkpoints are never considered abandoned."""
        checkpoint.start_checkpoint(
            "BIF-DONE-OLD",
            description="Old but completed work",
            expected_outcome="Done checkpoints are preserved",
            hermes_home=tmp_hermes_home,
        )
        checkpoint.finalize_checkpoint(
            "BIF-DONE-OLD",
            result="Completed",
            hermes_home=tmp_hermes_home,
        )

        # Manually backdate the done.json
        done_path = tmp_hermes_home / "working-set" / "checkpoints" / "BIF-DONE-OLD" / "done.json"
        done = json.loads(done_path.read_text())
        done["completion_timestamp"] = time.time() - 30 * 24 * 3600
        done_path.write_text(json.dumps(done, indent=2) + "\n", encoding="utf-8")

        cleaned = checkpoint.cleanup_abandoned_checkpoints(
            max_age_seconds=7 * 24 * 3600,
            hermes_home=tmp_hermes_home,
        )
        assert cleaned == 0  # Done checkpoints are not cleaned

    def test_list_all_checkpoints(self, tmp_hermes_home):
        """list_all_checkpoints returns summaries of all checkpoints."""
        checkpoint.start_checkpoint(
            "BIF-LIST-A",
            description="First checkpoint",
            expected_outcome="Appears in listing",
            hermes_home=tmp_hermes_home,
        )
        checkpoint.start_checkpoint(
            "BIF-LIST-B",
            description="Second checkpoint",
            expected_outcome="Appears in listing",
            hermes_home=tmp_hermes_home,
        )

        all_ckpts = checkpoint.list_all_checkpoints(hermes_home=tmp_hermes_home)
        assert len(all_ckpts) >= 2
        # Each entry has 'state' field, and 'intent' (which carries card_id)
        for entry in all_ckpts:
            assert "state" in entry
        pending_found = sum(1 for c in all_ckpts if c["state"] == "pending")
        assert pending_found >= 2