"""Exam generation: quotas, determinism and shortfall handling."""

from __future__ import annotations

import pytest

from app.services.exam_generator import ExamGenerationError, generate_exam
from tests.conftest import CCAO_F_ITEMS_AT_60


class TestComposition:
    def test_exam_has_the_requested_number_of_items(self, ccao_weights, healthy_bank):
        assert generate_exam(ccao_weights, healthy_bank, 60, seed=1).item_count == 60

    def test_domain_mix_matches_the_blueprint(self, ccao_weights, healthy_bank):
        exam = generate_exam(ccao_weights, healthy_bank, 60, seed=1)
        assert exam.per_domain == CCAO_F_ITEMS_AT_60

    def test_no_question_appears_twice(self, ccao_weights, healthy_bank):
        exam = generate_exam(ccao_weights, healthy_bank, 60, seed=1)
        assert len(set(exam.question_ids)) == exam.item_count

    def test_every_question_is_mapped_to_its_domain(self, ccao_weights, healthy_bank):
        exam = generate_exam(ccao_weights, healthy_bank, 60, seed=1)
        assert set(exam.domain_of_question) == set(exam.question_ids)

    def test_a_healthy_bank_produces_no_warning(self, ccao_weights, healthy_bank):
        exam = generate_exam(ccao_weights, healthy_bank, 60, seed=1)
        assert exam.composition_warning is None
        assert exam.is_complete

    def test_smaller_practice_set_still_respects_the_blueprint(self, ccao_weights, healthy_bank):
        exam = generate_exam(ccao_weights, healthy_bank, 30, seed=1)
        assert exam.item_count == 30
        assert sum(exam.per_domain.values()) == 30


class TestDeterminism:
    def test_the_same_seed_reproduces_the_same_exam(self, ccao_weights, healthy_bank):
        a = generate_exam(ccao_weights, healthy_bank, 60, seed=12345)
        b = generate_exam(ccao_weights, healthy_bank, 60, seed=12345)
        assert a.question_ids == b.question_ids

    def test_different_seeds_produce_different_exams(self, ccao_weights, healthy_bank):
        a = generate_exam(ccao_weights, healthy_bank, 60, seed=1)
        b = generate_exam(ccao_weights, healthy_bank, 60, seed=2)
        assert a.question_ids != b.question_ids

    def test_an_omitted_seed_is_generated_and_returned(self, ccao_weights, healthy_bank):
        exam = generate_exam(ccao_weights, healthy_bank, 60)
        assert isinstance(exam.seed, int)
        # The returned seed must actually reproduce the exam, or stored attempts
        # could not be replayed.
        replay = generate_exam(ccao_weights, healthy_bank, 60, seed=exam.seed)
        assert replay.question_ids == exam.question_ids

    def test_unshuffled_mode_groups_items_by_domain(self, ccao_weights, healthy_bank):
        exam = generate_exam(ccao_weights, healthy_bank, 60, seed=1, shuffle=False)
        seen_order = [exam.domain_of_question[q] for q in exam.question_ids]
        # Each domain should occupy one contiguous run.
        runs = [seen_order[0]]
        for code in seen_order[1:]:
            if code != runs[-1]:
                runs.append(code)
        assert len(runs) == len(set(runs))


class TestShortfallHandling:
    def test_a_starved_domain_still_yields_a_full_length_exam(self, ccao_weights, healthy_bank):
        starved = {**healthy_bank, "OEV": [9001, 9002, 9003]}
        exam = generate_exam(ccao_weights, starved, 60, seed=7)
        # The alternative -- a silently short exam -- would shrink raw_total and
        # distort the scaled score.
        assert exam.item_count == 60

    def test_a_starved_domain_contributes_only_what_it_has(self, ccao_weights, healthy_bank):
        starved = {**healthy_bank, "OEV": [9001, 9002, 9003]}
        exam = generate_exam(ccao_weights, starved, 60, seed=7)
        assert exam.per_domain["OEV"] == 3

    def test_the_shortfall_is_reported_not_hidden(self, ccao_weights, healthy_bank):
        starved = {**healthy_bank, "OEV": [9001, 9002, 9003]}
        exam = generate_exam(ccao_weights, starved, 60, seed=7)
        assert exam.composition_warning is not None
        assert "OEV" in exam.composition_warning

    def test_redistribution_never_exceeds_a_domain_available_questions(self, ccao_weights, healthy_bank):
        starved = {**healthy_bank, "OEV": [9001], "TRO": [9101, 9102]}
        exam = generate_exam(ccao_weights, starved, 60, seed=3)
        for code, count in exam.per_domain.items():
            assert count <= len(starved[code])

    def test_still_no_duplicates_after_redistribution(self, ccao_weights, healthy_bank):
        starved = {**healthy_bank, "OEV": [9001, 9002]}
        exam = generate_exam(ccao_weights, starved, 60, seed=3)
        assert len(set(exam.question_ids)) == exam.item_count

    def test_a_globally_exhausted_bank_reports_being_short(self, ccao_weights):
        tiny = {w.code: [i] for i, w in enumerate(ccao_weights)}  # 7 questions total
        exam = generate_exam(ccao_weights, tiny, 60, seed=1)
        assert exam.item_count == 7
        assert not exam.is_complete
        assert "short" in exam.composition_warning


class TestValidation:
    def test_zero_items_is_rejected(self, ccao_weights, healthy_bank):
        with pytest.raises(ExamGenerationError):
            generate_exam(ccao_weights, healthy_bank, 0)

    def test_an_entirely_empty_bank_is_rejected(self, ccao_weights):
        with pytest.raises(ExamGenerationError, match="empty"):
            generate_exam(ccao_weights, {w.code: [] for w in ccao_weights}, 60)
