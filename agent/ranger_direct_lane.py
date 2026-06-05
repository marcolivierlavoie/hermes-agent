"""Ranger direct-work lane prompt helpers for Biff Discord routing."""

from __future__ import annotations

from textwrap import dedent
from typing import Any


def build_ranger_direct_instruction(user_request: Any) -> str:
    """Return the compact handoff that makes Ranger own board administration."""

    request = " ".join(str(user_request or "").strip().split())
    if not request:
        request = "(No user request text was provided.)"

    return dedent(
        f"""
        Ranger direct Kanban administration lane selected.

        User request:
        {request}

        Execution contract:
        - Treat this as Kanban/backlog administration, not engineering implementation.
        - Ranger owns story creation, triage, movement, comments, archival, dependencies, and board hygiene.
        - Biff owns the user conversation; report concise done/blocked evidence back through the handoff.
        - Do not start implementation work unless Marco explicitly asked to work the story now.
        - If the request mentions Forge, Quill, or Vex work, update the board/assignment so the right specialist can do it; do not do their job yourself.
        - For board overview, use kanban_list. Use kanban_show only with an explicit task_id.
        - Keep story numbers, status, assignee, and comments consistent with the current native Kanban board.
        - Do not close a story unless the requested board action clearly says to close it or verified completion evidence exists.
        - Close with exactly what board item changed and any ambiguity that needs Marco.
        """
    ).strip()
