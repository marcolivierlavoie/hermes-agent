import json
from pathlib import Path

from tools.delegate_tool import _record_biff_delegation_telemetry


def test_biff_delegation_telemetry_is_local_jsonl_and_hashes_goal(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    result = _record_biff_delegation_telemetry(
        path=path,
        parent_session_id="parent-session",
        task_count=2,
        results=[
            {"task_index": 0, "status": "completed", "duration_seconds": 1.2, "api_calls": 3},
            {"task_index": 1, "status": "error", "duration_seconds": 0.4, "api_calls": 1, "error": "boom"},
        ],
        task_goals=["sensitive customer task", "another private task"],
        total_duration_seconds=1.6,
    )

    assert result is True
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "biff.delegation.completed"
    assert row["parent_session_hash"].startswith("session_")
    assert row["task_count"] == 2
    assert row["completed_count"] == 1
    assert row["error_count"] == 1
    assert row["task_goal_hashes"] == [
        "goal_f4967165c350",
        "goal_344f2a0bb262",
    ]
    assert "sensitive" not in path.read_text()


def test_biff_delegation_telemetry_best_effort_on_bad_path(tmp_path):
    bad_path = tmp_path / "not-dir" / "telemetry.jsonl"
    bad_path.parent.write_text("x")

    assert _record_biff_delegation_telemetry(
        path=bad_path,
        parent_session_id="sid",
        task_count=0,
        results=[],
        task_goals=[],
        total_duration_seconds=0,
    ) is False
