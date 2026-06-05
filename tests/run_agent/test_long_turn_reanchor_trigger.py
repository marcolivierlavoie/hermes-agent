from run_agent import AIAgent


def test_long_turn_reanchor_trigger_uses_word_boundaries_not_substrings():
    agent = AIAgent.__new__(AIAgent)

    assert agent._should_long_turn_reanchor("go") is True
    assert agent._should_long_turn_reanchor("continue this") is True
    assert agent._should_long_turn_reanchor("finish 612") is True
    assert agent._should_long_turn_reanchor("negotiate contract") is False
    assert agent._should_long_turn_reanchor("ongoing analysis") is False
