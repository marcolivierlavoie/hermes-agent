"""Quill direct-work lane prompt helpers for Biff Discord routing."""

from __future__ import annotations

from textwrap import dedent
from typing import Any


def build_quill_direct_instruction(user_request: Any) -> str:
    """Return the compact handoff that makes Quill own docs/research/memory."""

    request = " ".join(str(user_request or "").strip().split())
    if not request:
        request = "(No user request text was provided.)"

    return dedent(
        f"""
        Quill direct documentation and research lane selected.

        User request:
        {request}

        Execution contract:
        - Treat this as documentation, research synthesis, Obsidian, or Mnemosyne memory work.
        - Quill owns durable docs, summaries, research notes, runbooks, decision records, and memory updates.
        - Use primary sources or local evidence when research depends on current facts.
        - Do not perform engineering implementation unless Marco explicitly asks for code changes.
        - Use Kanban only as a ledger when the request asks for board/story changes.
        - Do not call kanban_show with no task_id; if there is no specific card id, use kanban_list or skip Kanban.
        - If the request reveals board/task hygiene, ask Ranger or update the board only as a ledger.
        - If the request requires QA validation, ask Vex or record that Vex should verify it.
        - Close with what was documented, where it was recorded, and any uncertainty.
        """
    ).strip()
