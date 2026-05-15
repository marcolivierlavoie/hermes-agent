from gateway.discord_operator_checklist import (
    BIF_533_EXAMPLE,
    DiscordChecklistItem,
    format_discord_operator_checklist,
)


def test_bif_533_example_is_compact_and_actionable():
    assert BIF_533_EXAMPLE == (
        "**BIF-533 — Discord compact operator checklist progress pattern**\n"
        "Implementing as a reusable formatter; gateway behavior unchanged.\n"
        "1/4 complete · active\n"
        "✅ inspect current gateway/display helpers\n"
        "🔄 define reusable Discord checklist format\n"
        "☐ document when to post vs stay silent\n"
        "☐ run focused tests/checks\n"
        "_quiet between milestones; no raw tool logs_"
    )


def test_formatter_sanitizes_multiline_raw_log_notes():
    msg = format_discord_operator_checklist(
        issue_id="BIF-533",
        title="Checklist",
        items=[
            DiscordChecklistItem(
                "avoid raw tool logs",
                "active",
                "```\nline 1\nline 2 with `cmd`\n```",
            )
        ],
        footer="no spam",
    )

    assert "```" not in msg
    assert "`" not in msg
    assert "line 1 line 2 with cmd" in msg
    assert msg.count("\n") == 3


def test_formatter_limits_visible_items_and_counts_all_items():
    msg = format_discord_operator_checklist(
        title="Long checklist",
        max_items=2,
        items=[
            ("one", "done"),
            ("two", "done"),
            ("three", "pending"),
            ("four", "blocked", "needs operator decision"),
        ],
    )

    assert "2/4 complete · blocked" in msg
    assert "✅ one" in msg
    assert "✅ two" in msg
    assert "three" not in msg
    assert "… +2 more" in msg
