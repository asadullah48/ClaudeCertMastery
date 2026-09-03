"""Item grading: MCQ, multi-response set equality and partial credit."""

from __future__ import annotations

import pytest

from app.services.scoring import (
    MasteryBand,
    QuestionType,
    ScoringError,
    compute_partial_credit,
    grade_item,
    score_attempt,
)


def mcq(selected, correct={1}, qid=1, domain="PTE"):
    return grade_item(qid, domain, QuestionType.MCQ, set(correct), set(selected))


def mr(selected, correct={1, 2}, qid=2, domain="OEV"):
    return grade_item(qid, domain, QuestionType.MR, set(correct), set(selected))


class TestMCQGrading:
    def test_correct_single_selection_is_correct(self):
        assert mcq({1}).is_correct

    def test_wrong_single_selection_is_incorrect(self):
        assert not mcq({2}).is_correct

    def test_unanswered_is_incorrect_and_marked_unanswered(self):
        r = mcq(set())
        assert not r.is_correct and not r.answered and r.partial_credit == 0.0

    def test_selecting_extra_options_on_an_mcq_is_incorrect(self):
        assert not mcq({1, 2}).is_correct

    def test_mcq_with_multiple_correct_options_is_rejected(self):
        with pytest.raises(ScoringError, match="exactly one"):
            grade_item(1, "PTE", QuestionType.MCQ, {1, 2}, {1})


class TestMRGrading:
    def test_exact_match_is_correct(self):
        assert mr({1, 2}).is_correct

    def test_partial_selection_is_incorrect_by_default_policy(self):
        # D-4: real exams award no credit for a partially right MR answer.
        r = mr({1})
        assert not r.is_correct
        assert r.partial_credit == 0.5  # still recorded for the dashboard

    def test_superset_selection_is_incorrect(self):
        assert not mr({1, 2, 3}).is_correct

    def test_selection_order_does_not_matter(self):
        assert mr({2, 1}).is_correct

    def test_mr_with_fewer_than_two_correct_options_is_rejected(self):
        with pytest.raises(ScoringError, match="at least two"):
            grade_item(2, "OEV", QuestionType.MR, {1}, {1})


class TestPartialCredit:
    def test_all_correct_earns_full_credit(self):
        assert compute_partial_credit({1, 2}, {1, 2}) == 1.0

    def test_half_correct_earns_half_credit(self):
        assert compute_partial_credit({1, 2}, {1}) == 0.5

    def test_selecting_every_option_earns_nothing(self):
        # Without the false-positive penalty this would score 1.0, making the least
        # informative possible answer look like mastery.
        assert compute_partial_credit({1, 2}, {1, 2, 3, 4, 5}) == 0.0

    def test_over_selection_is_penalised_not_merely_ignored(self):
        assert compute_partial_credit({1, 2}, {1, 2, 3}) == 0.5

    def test_entirely_wrong_selection_earns_nothing(self):
        assert compute_partial_credit({1, 2}, {3, 4}) == 0.0

    def test_credit_never_goes_negative(self):
        assert compute_partial_credit({1}, {2, 3, 4, 5}) == 0.0

    def test_question_with_no_correct_option_is_rejected(self):
        with pytest.raises(ScoringError):
            compute_partial_credit(set(), {1})


class TestMasteryBands:
    @pytest.mark.parametrize(
        "pct,band",
        [
            (0, MasteryBand.CRITICAL), (49.9, MasteryBand.CRITICAL),
            (50, MasteryBand.DEVELOPING), (69.9, MasteryBand.DEVELOPING),
            (70, MasteryBand.PROFICIENT), (84.9, MasteryBand.PROFICIENT),
            (85, MasteryBand.STRONG), (100, MasteryBand.STRONG),
        ],
    )
    def test_band_boundaries(self, pct, band):
        assert MasteryBand.from_percentage(pct) is band


class TestAttemptScoring:
    def test_domain_breakdown_counts_each_domain_separately(self):
        items = [mcq({1}, qid=i, domain="PTE") for i in range(4)]
        items += [mcq({2}, qid=10 + i, domain="OEV") for i in range(4)]
        result = score_attempt(items)
        by_code = {d.domain_code: d for d in result.domain_scores}
        assert by_code["PTE"].correct == 4 and by_code["PTE"].percentage == 100.0
        assert by_code["OEV"].correct == 0 and by_code["OEV"].percentage == 0.0

    def test_domain_totals_reconcile_with_the_raw_total(self):
        items = [mcq({1} if i % 2 == 0 else {2}, qid=i, domain="PTE") for i in range(10)]
        result = score_attempt(items)
        assert sum(d.total for d in result.domain_scores) == result.raw_total == 10
        assert sum(d.correct for d in result.domain_scores) == result.raw_correct == 5

    def test_42_of_60_correct_reports_exactly_the_pass_line(self):
        items = [mcq({1}, qid=i) for i in range(42)] + [mcq({2}, qid=100 + i) for i in range(18)]
        result = score_attempt(items)
        assert result.raw_correct == 42
        assert result.scaled_score == 720
        assert result.passed is True

    def test_41_of_60_correct_reports_a_fail(self):
        items = [mcq({1}, qid=i) for i in range(41)] + [mcq({2}, qid=100 + i) for i in range(19)]
        result = score_attempt(items)
        assert result.scaled_score < 720 and result.passed is False

    def test_domain_weighting_is_not_applied_to_the_score(self):
        # D-1: an exam of 10 heavy-domain items and 10 light-domain items, half correct
        # in each, must score exactly 50% raw -- weights govern composition, not scoring.
        items = [mcq({1}, qid=i, domain="OEV") for i in range(5)]
        items += [mcq({2}, qid=50 + i, domain="OEV") for i in range(5)]
        items += [mcq({1}, qid=100 + i, domain="TRO") for i in range(5)]
        items += [mcq({2}, qid=150 + i, domain="TRO") for i in range(5)]
        assert score_attempt(items).raw_percentage == 50.0

    def test_empty_attempt_is_rejected(self):
        with pytest.raises(ScoringError):
            score_attempt([])
