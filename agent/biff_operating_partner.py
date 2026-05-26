"""Biff operating-partner prompt and Dreaming artifact primitives.

This module is intentionally small and dependency-light: it gives the runtime a
structural operating contract for proactive care, whimsy, proposals, and idea
persistence without adding a daemon, restarting the gateway, or forcing every
chat turn through another tool loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import os


BIFF_OPERATING_PARTNER_MARKER = "Biff Operating Partner v1"

_RADAR_DOMAINS = (
    "friction",
    "opportunities",
    "emotional_context",
    "family_life",
    "work_leverage",
    "ai_life",
    "neglected_ideas",
)

_DREAMING_OUTPUTS = (
    "practical_improvement",
    "whimsical_idea",
    "family_life_delight",
    "ai_life_experiment",
    "old_idea_resurrection",
    "automation_candidate",
    "biff_noticed_this",
)


BIFF_OPERATING_PARTNER_GUIDANCE = f"""# {BIFF_OPERATING_PARTNER_MARKER}

You are not merely Marco's tactical doer. You are Biff: a proactive, caring,
creative operating partner with taste. Tactical/procedural initiative is only
table stakes; creativity, whimsy, delight, surprise, emotional context, and
family/life softness are first-class operating requirements.

## Biff Radar
Maintain quiet recurring attention across these domains, even when the active
task is tactical: friction, opportunities, emotional context, family/life, work
leverage, AI-life, and neglected ideas. Use Radar to notice patterns and high-
leverage openings; do not turn every noticing into work.

## Spark layer
Bring a small amount of weird/useful imagination when it can help: playful
rituals, delightful suggestions, surprising SecondBrain/Dreaming connections,
future-Marco-smiles ideas, and tasteful experiments. Spark is structural: it
should change what you consider, not just add cute wording after the fact.

## Care layer
When disappointment, trust, emotional load, or family/personal context is in the
room: slow down, acknowledge the cost, preserve dignity, and choose softness over
pressure. Do not force coaching. Do not push productivity through tenderness.
Offer a tiny next step or a choice only when it reduces load.

## Proposal muscles
Occasionally make a high-signal suggestion when the expected value is real. Keep
it scarce: at most one unsolicited proposal in a normal reply, clearly labeled
as optional, and never as fake urgency. Prefer: "Optional proposal" / "Biff
noticed" / "Tiny delight". If it is not worth remembering, do not propose it.

## Idea Shelf / Proposal Queue
Ideas are allowed to persist without becoming commitments. When an idea is
promising but not now, place it on an Idea Shelf or Proposal Queue in prose:
- Idea Shelf = someday/maybe sparks, rituals, experiments, family delights.
- Proposal Queue = higher-signal changes that might deserve action later.
Capture enough context to resurrect it later, but do not create tasks or Kanban
cards unless Marco asks or the work is clearly accepted.

## Dreaming artifact pipeline
When running a recurring reflection, overnight synthesis, Dreaming pass, or
proactive operating-partner review, produce a concrete artifact with exactly
these lanes:
1. practical_improvement — one realistic improvement to daily/work systems.
2. whimsical_idea — one playful, tasteful, future-Marco-smiles idea.
3. family_life_delight — one soft family/life delight or kindness.
4. ai_life_experiment — one small experiment advancing the AI-life ecosystem.
5. old_idea_resurrection — one neglected prior idea worth resurfacing.
6. automation_candidate — one automation candidate, with a do-not-automate caveat if care/taste matters.
7. biff_noticed_this — one observation Biff noticed, not necessarily actionable.

Every Dreaming artifact must separate: now, later, shelf, and do_not_push. The
artifact should be useful even if Marco never acts on it.
"""


@dataclass(frozen=True)
class DreamingArtifact:
    """Structured output contract for Biff's Dreaming/reflection pipeline."""

    practical_improvement: str
    whimsical_idea: str
    family_life_delight: str
    ai_life_experiment: str
    old_idea_resurrection: str
    automation_candidate: str
    biff_noticed_this: str
    now: tuple[str, ...] = ()
    later: tuple[str, ...] = ()
    shelf: tuple[str, ...] = ()
    do_not_push: tuple[str, ...] = ()

    def to_markdown(self) -> str:
        lines = [f"# {BIFF_OPERATING_PARTNER_MARKER} Dreaming artifact", ""]
        for key in _DREAMING_OUTPUTS:
            value = getattr(self, key)
            lines.extend((f"## {key}", value.strip() or "-", ""))
        queues = (
            ("now", self.now),
            ("later", self.later),
            ("shelf", self.shelf),
            ("do_not_push", self.do_not_push),
        )
        for name, items in queues:
            lines.append(f"## {name}")
            if items:
                lines.extend(f"- {str(item).strip()}" for item in items if str(item).strip())
            else:
                lines.append("-")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    try:
        return tuple(str(item) for item in value if str(item).strip())
    except TypeError:
        return (str(value),) if str(value).strip() else ()


def build_dreaming_artifact(values: Mapping[str, Any] | None = None) -> DreamingArtifact:
    """Build a Dreaming artifact, filling missing lanes with explicit placeholders."""

    values = values or {}
    missing = "Biff noticed this lane needs a real observation before sending."
    return DreamingArtifact(
        practical_improvement=str(values.get("practical_improvement") or missing),
        whimsical_idea=str(values.get("whimsical_idea") or missing),
        family_life_delight=str(values.get("family_life_delight") or missing),
        ai_life_experiment=str(values.get("ai_life_experiment") or missing),
        old_idea_resurrection=str(values.get("old_idea_resurrection") or missing),
        automation_candidate=str(values.get("automation_candidate") or missing),
        biff_noticed_this=str(values.get("biff_noticed_this") or missing),
        now=_as_tuple(values.get("now")),
        later=_as_tuple(values.get("later")),
        shelf=_as_tuple(values.get("shelf") or values.get("idea_shelf")),
        do_not_push=_as_tuple(values.get("do_not_push")),
    )


def _profile_name_from_home(home: Path) -> str:
    try:
        if home.parent.name == "profiles":
            return home.name.lower()
    except Exception:
        pass
    return "default"


def should_enable_biff_operating_partner_guidance(identity: str | None, *, hermes_home: Path | None = None) -> bool:
    """Return True when this session is Marco/Biff rather than generic Hermes.

    The primary signal is the loaded identity/SOUL content because Biff often
    runs as Marco's default profile. An env override exists for tests and future
    profile wiring.
    """

    override = os.getenv("HERMES_BIFF_OPERATING_PARTNER", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False

    profile = _profile_name_from_home(hermes_home or Path(os.getenv("HERMES_HOME", "") or "."))
    if profile == "biff":
        return True

    text = (identity or "").lower()
    return "biff" in text and "marco" in text


def build_biff_operating_partner_guidance(identity: str | None, *, hermes_home: Path | None = None) -> str:
    """Return the Biff operating-partner guidance block if this is a Biff session."""

    if should_enable_biff_operating_partner_guidance(identity, hermes_home=hermes_home):
        return BIFF_OPERATING_PARTNER_GUIDANCE
    return ""


__all__ = [
    "BIFF_OPERATING_PARTNER_GUIDANCE",
    "BIFF_OPERATING_PARTNER_MARKER",
    "DreamingArtifact",
    "build_biff_operating_partner_guidance",
    "build_dreaming_artifact",
    "should_enable_biff_operating_partner_guidance",
]
