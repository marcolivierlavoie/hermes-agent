"""Regression guards for external-memory prefetch staying internal.

Mnemosyne/Honcho/Supermemory prefetch is model context, not a new user
utterance. If it is appended to the user message, the model can answer as if the
user pasted the memory block, and UI/session surfaces may expose the
``<memory-context>`` wrapper. These tests keep the injection path and emitted
assistant text scrubbed.
"""

from __future__ import annotations

import inspect


class TestMemoryPrefetchInternalContext:
    def test_prefetch_context_is_system_suffix_not_user_message(self):
        from agent.conversation_loop import run_conversation

        src = inspect.getsource(run_conversation)
        user_injection_start = src.index("if idx == current_turn_user_idx")
        user_injection_end = src.index("# For ALL assistant messages", user_injection_start)
        user_injection_block = src[user_injection_start:user_injection_end]

        assert "build_memory_context_block(_ext_prefetch_cache)" not in user_injection_block
        assert "_memory_system_context = build_memory_context_block(_ext_prefetch_cache)" in src
        assert 'effective_system = (effective_system + "\\n\\n" + _memory_system_context).strip()' in src

    def test_final_response_scrubs_echoed_memory_context(self):
        from agent.conversation_loop import run_conversation

        src = inspect.getsource(run_conversation)
        assert "final_response = sanitize_context(agent._strip_think_blocks(final_response)).strip()" in src

    def test_interim_assistant_callback_scrubs_memory_context(self):
        from run_agent import AIAgent

        agent = AIAgent.__new__(AIAgent)
        setattr(agent, "_strip_think_blocks", lambda content: content)
        emitted: list[tuple[str, bool]] = []
        setattr(
            agent,
            "interim_assistant_callback",
            lambda text, already_streamed=False: emitted.append((text, already_streamed)),
        )
        setattr(agent, "_interim_content_was_streamed", lambda content: False)

        agent._emit_interim_assistant_message(
            {
                "role": "assistant",
                "content": (
                    "Before\n<memory-context>\n"
                    "Mnemosyne selective prefetch context: secret-ish internal note\n"
                    "</memory-context>\nAfter"
                ),
            }
        )

        assert emitted == [("Before\n\nAfter", False)]
