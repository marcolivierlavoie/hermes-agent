from gateway.run import _build_seamless_rollover_handoff


def test_rollover_handoff_warns_not_to_repeat_stale_tool_blockers():
    handoff = _build_seamless_rollover_handoff(
        old_session_id="old-session",
        new_session_id="new-session",
        threshold=70_000,
        prompt_tokens=73_694,
        context_length=272_000,
        user_text="Continue",
        response_text=(
            "I cannot act because this surface only exposes Mnemosyne/memory "
            "tooling — no shell, file, Kanban, or repo tools."
        ),
    )

    assert "Tool access is determined by the fresh turn's live tool schema" in handoff
    assert "do not repeat stale claims" in handoff
    assert "shell/file/Kanban/repo tools are unavailable" in handoff
    assert "Previous session: old-session" in handoff
    assert "Fresh session: new-session" in handoff
