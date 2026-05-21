"""Deterministic Biff skill-bundle auto-selection for gateway prompts.

This module intentionally stays small and local: no LLM calls, no embeddings, no
network, and no writes. Gateway dispatch calls it only for ordinary natural
language messages; explicit slash commands keep precedence in gateway/run.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class BiffBundleSelection:
    """A selected Biff bundle and quiet observability metadata."""

    command_key: str
    bundle_name: str
    score: int
    reason: str


_BIF_ISSUE_RE = re.compile(r"\bBIF-\d+\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://|\barxiv\.org\b|\byoutu(?:be\.com|\.be)\b", re.IGNORECASE)

# Ordered for deterministic tie-breaks. More specific workflow bundles come
# before broader governance/logistics options.
_RULES: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = (
    (
        "biff-automation-ownership",
        (
            (r"\bautomation\b|\bwebhook\b|\bwatcher\b|\bcron\b|\bn8n\b|\bmcp\b|\bdurable\b|\bsubscription\b", 4),
            (r"\bown(?:er|ership)?\b|\brunbook\b|\btrigger\b|\bschedule\b", 2),
        ),
    ),
    (
        "biff-hermes-runtime-change",
        (
            (r"\bhermes(?:[-\s]?agent)?\b|\bbiff runtime\b|\bgateway\b|\bdiscord\b|\bskill(?:[-\s]?bundle| authoring)?\b|\bconfig(?:\.yaml)?\b|\bdashboard\b", 4),
            (r"\bimplement\b|\bfix\b|\bchange\b|\bpatch\b|\btest\b|\bfixture\b|\brepo\b|\bruntime\b", 2),
        ),
    ),
    (
        "biff-issue-execution",
        (
            (r"\bBIF-\d+\b|\bLinear\b|\bissue\b|\bticket\b", 4),
            (r"\bcontinue\b|\bfinish\b|\bclose\b|\bacceptance\b|\bblocker\b|\bhandoff\b|\bforge\b|\bvex\b|\branger\b", 2),
        ),
    ),
    (
        "biff-research-to-decision",
        (
            (r"\bresearch\b|\bsynthesis\b|\bsynthesize\b|\bdecision\b|\brecommendation\b|\bbrief\b|\bpapers?\b|\bdocs?\b|\bvideo\b|\btranscript\b", 4),
            (r"\bcompare\b|\banalyze\b|\bevidence\b|\bsource\b|\barxiv\b|\byoutube\b", 2),
        ),
    ),
    (
        "biff-meeting-followup",
        (
            (r"\bmeeting\b|\bcalendar\b|\bteams\b|\bstandup\b|\bfollow[-\s]?up\b|\baction items?\b|\binbox\b|\bemail\b", 4),
            (r"\bprep\b|\bsummary\b|\bnotes?\b|\bdraft\b|\bremind(?:er)?\b", 2),
        ),
    ),
    (
        "biff-memory-knowledge-governance",
        (
            (r"\bmemory\b|\bmnemosyne\b|\bremember\b|\bforget\b|\bknowledge\b|\bobsidian\b|\bsecondbrain\b|\bsource of truth\b", 4),
            (r"\bdurable\b|\bgovernance\b|\bpreference\b|\bfact\b|\bcorrection\b|\bwriteback\b", 2),
        ),
    ),
    (
        "biff-personal-logistics",
        (
            (r"\breminder\b|\berrand\b|\bshopping\b|\bgrocery\b|\blist\b|\bhousehold\b|\bpersonal\b|\bmap\b|\bdirections\b", 4),
            (r"\badd\b|\bcapture\b|\bnote\b|\bdraft\b|\bmessage\b|\bimessage\b", 2),
        ),
    ),
)


def _slug_for_bundle(info: Mapping[str, Any], key: str) -> str:
    slug = str(info.get("slug") or "").strip()
    if slug:
        return slug
    name = str(info.get("name") or "").strip().lower().replace("_", "-").replace(" ", "-")
    return name or key.lstrip("/")


def should_attempt_biff_bundle_auto_selection(command: str | None) -> bool:
    """Return True only for non-slash natural-language gateway turns."""
    return not (command or "").strip()


def select_biff_bundle_for_prompt(
    text: str,
    bundles: Mapping[str, Mapping[str, Any]],
    *,
    min_score: int = 4,
) -> BiffBundleSelection | None:
    """Return the best existing ``/biff-*`` bundle for a natural-language prompt.

    The selector is deterministic and conservative:
    - considers only bundles already present in ``bundles``;
    - requires at least one strong workflow signal (default score >= 4);
    - never interprets slash commands; callers should skip auto-selection when
      ``MessageEvent.get_command()`` returns a command.
    """
    body = " ".join(str(text or "").strip().split())
    if not body:
        return None

    available: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for key, info in (bundles or {}).items():
        if not isinstance(info, Mapping):
            continue
        slug = _slug_for_bundle(info, key)
        if slug.startswith("biff-"):
            available[slug] = (key, info)
    if not available:
        return None

    # URL-only/source prompts should favor research if that bundle exists.
    url_bonus = bool(_URL_RE.search(body))

    best: BiffBundleSelection | None = None
    for slug, patterns in _RULES:
        found = available.get(slug)
        if not found:
            continue
        score = 0
        reasons: list[str] = []
        for pattern, weight in patterns:
            if re.search(pattern, body, re.IGNORECASE):
                score += weight
                reasons.append(pattern)
        if slug == "biff-research-to-decision" and url_bonus:
            score += 4
            reasons.append("url/source")
        # Named BIF issues are still useful context for runtime/code prompts,
        # but should not overpower clear Hermes-runtime work.
        if slug == "biff-issue-execution" and _BIF_ISSUE_RE.search(body):
            reasons.append("BIF issue id")

        if score < min_score:
            continue
        key, info = found
        candidate = BiffBundleSelection(
            command_key=key,
            bundle_name=str(info.get("name") or slug),
            score=score,
            reason=", ".join(reasons[:3]) or "matched deterministic workflow rule",
        )
        if best is None or candidate.score > best.score:
            best = candidate

    return best
