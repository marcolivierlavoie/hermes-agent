from tools.chat_guardrails import (
    ChatToolPolicy,
    apply_chat_tool_policy,
    clear_chat_tool_policy,
    is_broad_shell_search,
    set_chat_tool_policy,
)


def test_broad_shell_search_detection_blocks_unscoped_patterns():
    assert is_broad_shell_search("grep -rnH something .")
    assert is_broad_shell_search("find . -name '*.py'")
    assert is_broad_shell_search("rg migration")


def test_scoped_rg_is_allowed():
    assert not is_broad_shell_search("rg migration gateway tests")
    assert not is_broad_shell_search("rg -n migration /Users/marco/.hermes/SOUL.md")


def test_chat_policy_caps_terminal_timeout_and_blocks_broad_search():
    task_id = "chat-session"
    set_chat_tool_policy(
        task_id,
        ChatToolPolicy(max_terminal_timeout=15, block_broad_shell_search=True),
    )
    try:
        args, error = apply_chat_tool_policy(
            "terminal",
            {"command": "pytest tests/foo.py", "timeout": 120},
            task_id=task_id,
        )
        assert error is None
        assert args["timeout"] == 15

        _args, error = apply_chat_tool_policy(
            "terminal",
            {"command": "grep -rnH speed ."},
            task_id=task_id,
        )
        assert error is not None
        assert "Kanban" in error
    finally:
        clear_chat_tool_policy(task_id)


def test_chat_policy_is_task_scoped():
    set_chat_tool_policy("guarded", ChatToolPolicy(max_terminal_timeout=10))
    try:
        args, error = apply_chat_tool_policy(
            "terminal",
            {"command": "pytest", "timeout": 120},
            task_id="other",
        )
        assert error is None
        assert args["timeout"] == 120
    finally:
        clear_chat_tool_policy("guarded")


def test_chat_policy_caps_total_live_tool_calls():
    task_id = "budgeted"
    set_chat_tool_policy(task_id, ChatToolPolicy(max_tool_calls=2))
    try:
        assert apply_chat_tool_policy("search_files", {"query": "a"}, task_id=task_id)[1] is None
        assert apply_chat_tool_policy("read_file", {"path": "x"}, task_id=task_id)[1] is None

        _args, error = apply_chat_tool_policy("terminal", {"command": "pwd"}, task_id=task_id)

        assert error is not None
        assert "live Discord tool budget" in error
    finally:
        clear_chat_tool_policy(task_id)


def test_chat_policy_blocks_repeated_identical_searches_before_looping():
    task_id = "repeated-search"
    set_chat_tool_policy(task_id, ChatToolPolicy(max_repeated_search_calls=2))
    try:
        call = {"pattern": "cockpit", "path": "web/src", "target": "content"}
        assert apply_chat_tool_policy("search_files", call, task_id=task_id)[1] is None
        assert apply_chat_tool_policy("search_files", call, task_id=task_id)[1] is None

        _args, error = apply_chat_tool_policy("search_files", call, task_id=task_id)

        assert error is not None
        assert "repeating" in error
        assert "Kanban/background" in error
    finally:
        clear_chat_tool_policy(task_id)


def test_chat_policy_allows_changed_search_angle():
    task_id = "changed-search"
    set_chat_tool_policy(task_id, ChatToolPolicy(max_repeated_search_calls=1))
    try:
        assert apply_chat_tool_policy(
            "search_files",
            {"pattern": "cockpit", "path": "web/src"},
            task_id=task_id,
        )[1] is None
        assert apply_chat_tool_policy(
            "search_files",
            {"pattern": "cockpit", "path": "tests"},
            task_id=task_id,
        )[1] is None
    finally:
        clear_chat_tool_policy(task_id)
