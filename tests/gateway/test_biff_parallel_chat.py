from gateway.biff_parallel_chat import should_use_biff_parallel_chat_lane


def test_parallel_chat_lane_accepts_direct_question_while_discord_busy():
    use_lane, reason = should_use_biff_parallel_chat_lane(
        "What can I make for dinner with eggs and rice?",
        platform_key="discord",
        running_agent=True,
    )

    assert use_lane is True
    assert "answer_now" in reason


def test_parallel_chat_lane_accepts_quick_status_check():
    use_lane, reason = should_use_biff_parallel_chat_lane(
        "Can you check gateway status?",
        platform_key="discord",
        running_agent=True,
    )

    assert use_lane is True
    assert "one_tool" in reason


def test_parallel_chat_lane_accepts_casual_quick_web_lookup():
    use_lane, reason = should_use_biff_parallel_chat_lane(
        "Can you look up deals online for a standing desk?",
        platform_key="discord",
        running_agent=True,
    )

    assert use_lane is True
    assert "quick_web" in reason


def test_parallel_chat_lane_rejects_workflow_work():
    use_lane, reason = should_use_biff_parallel_chat_lane(
        "Implement the remaining speed stories and update Kanban.",
        platform_key="discord",
        running_agent=True,
    )

    assert use_lane is False
    assert "main work lane" in reason


def test_parallel_chat_lane_is_discord_running_message_only():
    assert should_use_biff_parallel_chat_lane("What now?", platform_key="slack", running_agent=True)[0] is False
    assert should_use_biff_parallel_chat_lane("What now?", platform_key="discord", running_agent=False)[0] is False
    assert should_use_biff_parallel_chat_lane("What now?", platform_key="discord", command=True)[0] is False
