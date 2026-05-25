import json

from agent.biff_continuation_context import (
    build_resume_context_injection,
    checkpoint_path_for_session,
    is_refresh_resume_prompt,
    write_continuation_checkpoint,
)


def test_refresh_resume_prompt_detects_non_kanban_context_requests():
    assert is_refresh_resume_prompt("the chat refreshed again so I can't see your progress")
    assert is_refresh_resume_prompt("where did we leave off?")
    assert is_refresh_resume_prompt("pick up the non-Kanban thing")


def test_refresh_resume_prompt_does_not_catch_ordinary_work_requests():
    assert not is_refresh_resume_prompt("implement these stories")
    assert not is_refresh_resume_prompt("continue K-1399")


def test_write_continuation_checkpoint_persists_non_kanban_thread_state(tmp_path):
    checkpoint = write_continuation_checkpoint(
        session_key="discord:-100:42",
        platform="discord",
        user_text="Help me think through the household plan",
        assistant_text="We narrowed it to two options and chose the lower-risk one.",
        route_action="route_bundle",
        route_reason="workflow request may benefit from bundle context",
        root=tmp_path,
    )

    path = checkpoint_path_for_session("discord:-100:42", root=tmp_path)
    data = json.loads(path.read_text())
    current = json.loads((tmp_path / "current.json").read_text())

    assert checkpoint.session_key == "discord:-100:42"
    assert data["context_kind"] == "scratch"
    assert data["kanban_refs"] == []
    assert data["next_resume_action"] == "Use recent session history plus this scratch checkpoint before asking Marco to reconstruct context."
    assert current["session_key"] == "discord:-100:42"


def test_write_continuation_checkpoint_marks_kanban_refs_without_requiring_kanban(tmp_path):
    checkpoint = write_continuation_checkpoint(
        session_key="discord:home",
        platform="discord",
        user_text="Continue K-1399 after K-1398",
        assistant_text="K-1398 is done; K-1399 remains open.",
        route_action="kanban_status",
        route_reason="read-only Kanban/status request",
        root=tmp_path,
    )

    assert checkpoint.context_kind == "kanban"
    assert checkpoint.kanban_refs == ["K-1398", "K-1399"]


def test_build_resume_context_injection_uses_scratch_checkpoint_without_forcing_kanban(tmp_path):
    write_continuation_checkpoint(
        session_key="discord:abc",
        platform="discord",
        user_text="Compare the two approaches for the family calendar",
        assistant_text="We decided to keep it simple and avoid a new workflow.",
        route_action="route_bundle",
        route_reason="workflow request may benefit from bundle context",
        root=tmp_path,
    )

    injection = build_resume_context_injection(
        "where did we leave off?",
        session_key="discord:abc",
        root=tmp_path,
    )

    assert injection is not None
    assert "[Biff refresh/resume context]" in injection
    assert "Context kind: scratch" in injection
    assert "Do not force this into Kanban" in injection
    assert "Compare the two approaches" in injection
    assert "where did we leave off?" in injection


def test_build_resume_context_injection_mentions_kanban_only_when_refs_exist(tmp_path):
    write_continuation_checkpoint(
        session_key="discord:abc",
        platform="discord",
        user_text="Resume K-1400",
        assistant_text="K-1400 is in triage.",
        route_action="kanban_status",
        route_reason="read-only Kanban/status request",
        root=tmp_path,
    )

    injection = build_resume_context_injection(
        "resume",
        session_key="discord:abc",
        root=tmp_path,
    )

    assert injection is not None
    assert "Context kind: kanban" in injection
    assert "Check Kanban refs: K-1400" in injection
