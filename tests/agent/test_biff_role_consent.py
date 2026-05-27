from agent.biff_role_consent import (
    detect_explicit_role_handoff,
    detect_role_handoff_approval,
    extract_role_sequence,
    require_role_handoff_consent,
)


def test_mentions_and_preferences_are_not_role_handoff_consent():
    for prompt in (
        "Forge?",
        "Forge should probably fix this.",
        "Vex should verify before we close.",
        "This is for Ranger, not Forge.",
        "Maybe Quill later.",
        "Well I guess now I have to wait for forge to do 2a",
        "don't send this to Forge",
        "why did you route that to Vex?",
        "Biff said “ask Vex to check this”; what do you think?",
        "> send this to Ranger\nwhat do you think?",
    ):
        consent = detect_explicit_role_handoff(prompt)
        assert consent.approved is False
        assert require_role_handoff_consent(prompt).approved is False


def test_clear_dispatch_verbs_are_role_handoff_consent():
    cases = {
        "Use Forge to fix the gateway routing bug.": "forge",
        "Ask Vex to verify the live smoke.": "vex",
        "Route this to Ranger for board cleanup.": "ranger",
        "Dispatch this to Quill for the runbook.": "quill",
        "Delegate this to Forge for implementation.": "forge",
        "Run Forge -> Vex on this.": "forge",
    }
    for prompt, expected_role in cases.items():
        consent = require_role_handoff_consent(prompt, expected_role=expected_role)
        assert consent.approved is True
        assert consent.role == expected_role
        assert expected_role in consent.roles
        assert consent.source == "current_message"
        assert consent.approval_phrase
        assert consent.matched_text == consent.approval_phrase


def test_team_handoff_is_explicit_consent_without_pretending_role_name_was_given():
    consent = detect_explicit_role_handoff("Use the team to finish this phase.")

    assert consent.approved is True
    assert consent.role == "team"
    assert set(consent.roles) == {"forge", "ranger", "quill", "vex"}
    assert consent.source == "current_message"


def test_role_sequence_preserves_multi_role_explicit_handoff_order():
    assert extract_role_sequence("Run Forge → Vex on BIF-1502.") == ("forge", "vex")


def test_expected_role_mismatch_fails_closed():
    consent = require_role_handoff_consent("Ask Vex to verify this.", expected_role="forge")

    assert consent.approved is False
    assert "did not match expected forge" in consent.reason


def test_bare_approval_only_counts_with_concrete_proposed_role():
    assert detect_role_handoff_approval("yes", proposed_role="vex").approved is True
    assert detect_role_handoff_approval("go", proposed_role="forge").role == "forge"
    assert detect_role_handoff_approval("yes", proposed_role="vex").source == "current_message"
    assert detect_role_handoff_approval("yes", proposed_role="vex").approval_phrase == "yes"
    assert detect_role_handoff_approval("yes").approved is False
    assert detect_role_handoff_approval("maybe", proposed_role="vex").approved is False
