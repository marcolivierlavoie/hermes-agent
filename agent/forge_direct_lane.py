"""Forge direct-work lane prompt helpers for Biff Discord routing."""

from __future__ import annotations

from textwrap import dedent
from typing import Any


FORGE_DIRECT_BUNDLE_KEY = "/biff-hermes-runtime-change"


def build_forge_direct_instruction(user_request: Any) -> str:
    """Return the compact handoff that makes Forge act like the coding lane.

    Biff still owns the conversation, but engineering/config/runtime work should
    be dispatched with a clear execution contract instead of drifting through
    generic planning or board administration first.
    """

    request = " ".join(str(user_request or "").strip().split())
    if not request:
        request = "(No user request text was provided.)"

    return dedent(
        f"""
        Forge direct engineering lane selected.

        User request:
        {request}

        Execution contract:
        - Treat this as active engineering/debug/configuration work, not triage.
        - Work from `/Users/marco/.hermes/hermes-agent-biff-runtime` unless Marco explicitly names another repository or host.
        - Delegate to Forge immediately with this full request when delegation is available; if delegation is unavailable, follow the same Forge contract in the current turn.
        - Use Kanban as the lightweight ledger only: create, claim, or update the relevant card, but do not route the user through Ranger before work starts.
        - Do not call kanban_show with no task_id; that form is only valid for dispatcher-spawned Kanban workers.
        - Inspect the code/config first with narrow searches, preferring rg and bounded paths.
        - Make the smallest coherent change; do not revert unrelated user work.
        - Run focused tests or operational checks that match the changed surface.
        - For dashboard/frontend changes, rebuild the web bundle and verify the served source/UI without restarting services unless the user explicitly approved a restart in the current thread.
        - Do not restart the Hermes gateway/dashboard from a role lane. Service restarts require explicit Biff/controller approval for the current task and must use the system LaunchDaemon path only; status checks must use direct launchctl/health probes, not restart wrapper scripts.
        - Do not report the task as done until build/restart/live verification steps have either passed or you have named the specific blocker.
        - Ask Marco only for a true human blocker or a very high-risk decision.
        - Use Vex, Quill, Ranger, or other specialists only when the task actually needs their role.
        - Close with changed files, verification performed, restart status if relevant, and any residual risk.
        """
    ).strip()
