"""The example question in the prompt is copied almost verbatim by the model.

Regression: the example "What was the bet behind that strategy?" was repeated in
interviews across roles, and the candidate did not understand the word "bet".
"""

from bot.brain.prompting import VOICE_RULES


def test_the_one_question_example_does_not_use_the_bet_jargon():
    assert "Say this:" in VOICE_RULES
    assert "bet behind" not in VOICE_RULES.split("Say this:")[1].split("YOUR OUTPUT")[0]
