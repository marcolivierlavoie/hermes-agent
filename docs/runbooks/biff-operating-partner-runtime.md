# Biff operating-partner runtime overlay

BIF-1353 adds a structural runtime overlay for Marco/Biff sessions. It is not a cosmetic tone pass: `agent/system_prompt.py` conditionally injects `agent/biff_operating_partner.py` when the loaded identity/SOUL content is clearly Marco+Biff, when the profile home is `profiles/biff`, or when `HERMES_BIFF_OPERATING_PARTNER=1` is set.

## What changes

The overlay makes these first-class operating responsibilities:

- Biff Radar: quiet recurring attention to friction, opportunities, emotional context, family/life, work leverage, AI-life, and neglected ideas.
- Spark layer: weird/useful experiments, playful rituals, future-Marco-smiles suggestions, tasteful surprise, and surprising SecondBrain/Dreaming connections.
- Care layer: softer handling of disappointment, trust, emotional load, family/personal context, and explicit guidance not to force coaching or productivity through tenderness.
- Proposal muscles: scarce, high-signal optional proposals rather than spam.
- Idea Shelf / Proposal Queue: ideas can persist without becoming fake work or Kanban tasks.
- Dreaming artifact pipeline: a concrete artifact with practical, whimsical, family/life, AI-life, old-idea, automation, and “Biff noticed this” lanes.

## Dreaming artifact lanes

Recurring reflection / overnight Dreaming outputs should include:

1. `practical_improvement`
2. `whimsical_idea`
3. `family_life_delight`
4. `ai_life_experiment`
5. `old_idea_resurrection`
6. `automation_candidate`
7. `biff_noticed_this`

And queue sections:

- `now`
- `later`
- `shelf`
- `do_not_push`

`build_dreaming_artifact()` in `agent/biff_operating_partner.py` provides the minimal structured contract and markdown rendering for checks or future Dreaming hooks.

## Verification

Focused tests live in `tests/agent/test_biff_operating_partner.py` and prove:

- Generic Hermes prompts are unchanged.
- Marco/Biff identity gets the overlay.
- A `profiles/biff` home gets the overlay.
- The Dreaming artifact includes all required lanes and separates now/later/shelf/do_not_push.
