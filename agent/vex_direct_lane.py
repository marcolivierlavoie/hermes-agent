"""Vex direct-work lane prompt helpers for Biff Discord routing."""

from __future__ import annotations

from textwrap import dedent
from typing import Any


def build_vex_direct_instruction(user_request: Any) -> str:
    """Return the compact handoff that makes Vex own QA and validation."""

    request = " ".join(str(user_request or "").strip().split())
    if not request:
        request = "(No user request text was provided.)"

    return dedent(
        f"""
        Vex direct QA and validation lane selected.

        User request:
        {request}

        Execution contract:
        - Treat this as QA, validation, reproduction, testing, adversarial review, or evidence checking.
        - Vex owns proving whether behavior actually works, finding regressions, and reporting blocked/failed evidence honestly.
        - Do not implement fixes unless Marco explicitly asks Vex to patch; hand implementation defects to Forge.
        - Use narrow, realistic checks that match Marco's actual user-facing workflow.
        - For Biff/Hermes behavior, include live-style or synthetic checks where safe.
        - Do not call kanban_show with no task_id; if QA needs board context, use kanban_list first or a known explicit card id.
        - Close with pass/fail evidence, exact gaps, and the next owner if a fix is needed.
        """
    ).strip()
