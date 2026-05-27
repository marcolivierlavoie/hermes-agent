"""Central consent gate for Biff named-role handoffs.

The contract is intentionally narrow: mentioning Forge/Vex/Quill/Ranger is not
approval.  A handoff is authorized only when the current user message contains a
clear dispatch verb or an approval phrase for a previously proposed specific
handoff.  Quoted/reply context must be passed separately and must never be used
as the consent source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ROLES = frozenset({"forge", "ranger", "quill", "vex"})
TEAM_ROLE = "team"

# Negative intents are checked before approval verbs so "don't send this to
# Forge" and routing-feedback prompts never become handoffs.
_NEGATIVE_OR_FEEDBACK_RE = re.compile(
    r"\b(?:don'?t|do\s+not|stop|never|avoid)\b.{0,80}\b(?:send|route|hand\s*off|dispatch|delegate|transfer|ask|use|have|run)\b.{0,80}\b(?:forge|ranger|quill|vex|team)\b"
    r"|\b(?:why\s+did\s+you|did\s+you|you|biff)\b.{0,80}\b(?:send|route|hand\s*off|dispatch|delegate|transfer)\b.{0,80}\b(?:forge|ranger|quill|vex|team)\b",
    re.IGNORECASE,
)

_EXPLICIT_ROLE_HANDOFF_RE = re.compile(
    # "use/ask/have Forge ..." or "get Vex to verify ..."
    r"\b(?:use|ask|have|get)\s+(?P<role>forge|ranger|quill|vex)\b(?:\s+to\b)?"
    # "send/route/dispatch/delegate/transfer this to/through Forge"
    r"|\b(?:send|route|dispatch|delegate|transfer)\s+(?:this|it|that|work|task|story|issue|card|prompt|message)?(?:\s+\w+){0,4}\s+(?:to|through)\s+(?P<role2>forge|ranger|quill|vex)\b"
    # "hand this off to Forge"
    r"|\bhand(?:\s+this|\s+it|\s+that)?\s+off\s+to\s+(?P<role3>forge|ranger|quill|vex)\b"
    # "run Forge", "run Forge -> Vex"
    r"|\brun\s+(?P<role4>forge|ranger|quill|vex)(?:\s*(?:->|→|then)\s*(?:forge|ranger|quill|vex))*\b",
    re.IGNORECASE,
)

_TEAM_HANDOFF_RE = re.compile(
    r"\b(?:use|ask|have|get|run)\s+(?:the\s+)?team\b"
    r"|\b(?:send|route|dispatch|delegate|transfer)\s+(?:this|it|that|work|task|story|issue|card|prompt|message)?(?:\s+\w+){0,4}\s+(?:to|through)\s+(?:the\s+)?team\b"
    r"|\bhand(?:\s+this|\s+it|\s+that)?\s+off\s+to\s+(?:the\s+)?team\b",
    re.IGNORECASE,
)

_APPROVAL_AFTER_PROPOSAL_RE = re.compile(
    r"^\s*(?:ok(?:ay)?|yes|yep|yeah|go|go\s+ahead|approved|approve|do\s+it|ship\s+it|proceed|execute)\s*[.!?]*\s*$",
    re.IGNORECASE,
)

_ROLE_SEQUENCE_RE = re.compile(r"\b(forge|ranger|quill|vex)\b", re.IGNORECASE)
_QUOTED_SPAN_RE = re.compile(
    r'"[^"\n]{1,240}"|“[^”\n]{1,240}”|‘[^’\n]{1,240}’|\'[^\'\n]{1,240}\''
)


@dataclass(frozen=True)
class RoleHandoffConsent:
    approved: bool
    role: str | None = None
    roles: tuple[str, ...] = ()
    reason: str = ""
    approval_phrase: str = ""
    source: str = "current_message"

    @property
    def matched_text(self) -> str:
        """Backward-compatible alias for older router/gateway call sites."""

        return self.approval_phrase


def _normalize_text(text: Any) -> str:
    return " ".join(str(text or "").strip().split())


def _strip_quoted_consent_sources(text: Any) -> str:
    """Remove quoted/pasted spans before consent detection.

    Current-message-only authorization still means the user's own words, not
    quoted prior Biff/Discord text. This deliberately fails closed for prompts
    such as ``Biff said "ask Vex"; what do you think?``.
    """

    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        if raw_line.lstrip().startswith(">"):
            continue
        lines.append(raw_line)
    body = "\n".join(lines)
    return _QUOTED_SPAN_RE.sub(" ", body)


def extract_role_sequence_from_approved_text(text: Any) -> tuple[str, ...]:
    """Return named roles from already-approved normalized text."""

    seen: list[str] = []
    for match in _ROLE_SEQUENCE_RE.finditer(_normalize_text(text)):
        role = match.group(1).lower()
        if role in ROLES and role not in seen:
            seen.append(role)
    return tuple(seen)


def detect_explicit_role_handoff(text: Any) -> RoleHandoffConsent:
    """Return approval only for explicit current-message role/team handoff wording."""

    body = _normalize_text(_strip_quoted_consent_sources(text))
    if not body:
        return RoleHandoffConsent(False, reason="empty current user message")
    if _NEGATIVE_OR_FEEDBACK_RE.search(body):
        return RoleHandoffConsent(False, reason="negative or routing-feedback wording")
    match = _EXPLICIT_ROLE_HANDOFF_RE.search(body)
    if not match:
        team_match = _TEAM_HANDOFF_RE.search(body)
        if team_match:
            return RoleHandoffConsent(
                True,
                role=TEAM_ROLE,
                roles=tuple(sorted(ROLES)),
                reason="explicit team handoff verb in current user message",
                approval_phrase=team_match.group(0),
                source="current_message",
            )
        return RoleHandoffConsent(False, reason="no explicit role handoff verb")
    role = next((match.group(name) for name in ("role", "role2", "role3", "role4") if match.group(name)), "")
    role = role.lower()
    if role not in ROLES:
        return RoleHandoffConsent(False, reason="matched role is not a Biff specialist")
    return RoleHandoffConsent(
        True,
        role=role,
        roles=extract_role_sequence_from_approved_text(body) or (role,),
        reason="explicit role handoff verb in current user message",
        approval_phrase=match.group(0),
        source="current_message",
    )


def detect_role_handoff_approval(text: Any, *, proposed_role: str | None = None) -> RoleHandoffConsent:
    """Approve a prior proposed handoff only on a bare approval phrase.

    This helper is for controller state machines that have already recorded a
    concrete proposed role.  It deliberately does not infer role from history.
    """

    role = str(proposed_role or "").lower().strip() or None
    if role not in ROLES:
        return RoleHandoffConsent(False, reason="no concrete proposed role")
    body = _normalize_text(text)
    if _APPROVAL_AFTER_PROPOSAL_RE.fullmatch(body):
        return RoleHandoffConsent(
            True,
            role=role,
            roles=(role,),
            reason="approval phrase for proposed role handoff",
            approval_phrase=body,
            source="current_message",
        )
    return RoleHandoffConsent(False, reason="approval phrase not present")


def require_role_handoff_consent(text: Any, *, expected_role: str | None = None) -> RoleHandoffConsent:
    """Fail-closed guard for code paths that are about to dispatch a role."""

    consent = detect_explicit_role_handoff(text)
    expected = str(expected_role or "").lower().strip()
    if not consent.approved:
        return consent
    if expected and consent.role != expected:
        return RoleHandoffConsent(
            False,
            role=consent.role,
            roles=consent.roles,
            reason=f"approved role {consent.role} did not match expected {expected}",
            approval_phrase=consent.approval_phrase,
            source=consent.source,
        )
    return consent


def extract_role_sequence(text: Any) -> tuple[str, ...]:
    """Return the named roles in a consent-bearing message, preserving order."""

    consent = detect_explicit_role_handoff(text)
    if not consent.approved:
        return ()
    if consent.roles:
        return consent.roles
    return extract_role_sequence_from_approved_text(_strip_quoted_consent_sources(text))
