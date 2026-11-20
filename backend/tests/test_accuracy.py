"""Transcript accuracy (task 26): the word error rate is computed right (positive controls)."""

from feedback.accuracy import edit_counts, normalise, term_found, word_error_rate

REFERENCE = "We run everything on Kubernetes and the orders service talks to PostgreSQL over gRPC"  # 14 words


def test_a_perfect_transcript_scores_zero():
    assert word_error_rate(REFERENCE, "We run everything on Kubernetes, and the orders service talks to PostgreSQL over gRPC.") == 0


def test_known_inserted_errors_give_the_expected_rate():
    # 2 substitutions (Kubernetes->communities, gRPC->GRP), 1 deletion (everything), 1 insertion (really).
    hypothesis = "We run on communities and the orders service really talks to PostgreSQL over GRP"
    counts = edit_counts(normalise(REFERENCE), normalise(hypothesis))
    assert (counts["substitutions"], counts["deletions"], counts["insertions"]) == (2, 1, 1)
    assert word_error_rate(REFERENCE, hypothesis) == 4 / 14


def test_numbers_compare_by_value_not_by_spelling():
    assert normalise("thirty eight percent in two thousand nineteen") == normalise("38% in 2019")
    assert word_error_rate("We cut failures by 38 percent", "We cut failures by thirty-eight percent.") == 0
    assert word_error_rate("2.4 million", "2.4 million") == 0


def test_a_term_is_found_only_whole_and_in_order():
    assert term_found("Priya Raghunathan", "I reported to Priya Raghunathan last year")
    assert not term_found("Priya Raghunathan", "I reported to Priya Ragunathan last year")
    assert term_found("99.95 percent", "uptime was 99.95% all year")


def test_spoken_decimals_and_joined_hundreds_compare_by_value():
    assert normalise("ninety-nine point nine five percent") == normalise("99.95 percent")
    assert normalise("two hundred and fifty milliseconds") == normalise("250 milliseconds")
    assert normalise("fish and chips") == ["fish", "and", "chips"]
