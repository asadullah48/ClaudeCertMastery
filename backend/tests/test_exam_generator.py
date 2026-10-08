"""Exam generation: quotas, determinism and shortfall handling."""

from __future__ import annotations

import pytest

from collections import Counter
from types import SimpleNamespace

from app.services.exam_generator import (
    ExamGenerationError,
    display_label,
    generate_exam,
    order_options,
)
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


def _four_options(bank: dict[str, list[int]]) -> dict[int, list[int]]:
    """Give every question four option ids, the first being the authored key."""
    return {qid: [qid * 10 + k for k in range(4)] for ids in bank.values() for qid in ids}


class TestOptionShuffle:
    def test_the_same_seed_gives_the_same_option_order(self, ccao_weights, healthy_bank):
        opts = _four_options(healthy_bank)
        a = generate_exam(ccao_weights, healthy_bank, 60, seed=77, options=opts)
        b = generate_exam(ccao_weights, healthy_bank, 60, seed=77, options=opts)
        assert a.option_order == b.option_order

    def test_every_presented_item_has_a_permutation_of_its_options(
        self, ccao_weights, healthy_bank
    ):
        opts = _four_options(healthy_bank)
        exam = generate_exam(ccao_weights, healthy_bank, 60, seed=5, options=opts)
        assert set(exam.option_order) == set(exam.question_ids)
        for qid, order in exam.option_order.items():
            assert sorted(order) == opts[qid]

    def test_shuffling_options_does_not_change_which_questions_a_seed_draws(
        self, ccao_weights, healthy_bank
    ):
        # Stored attempts replay from their seed; adding the option shuffle must not
        # have moved any existing seed onto a different question set or order.
        opts = _four_options(healthy_bank)
        for seed in range(20):
            plain = generate_exam(ccao_weights, healthy_bank, 60, seed=seed)
            shuffled = generate_exam(ccao_weights, healthy_bank, 60, seed=seed, options=opts)
            assert plain.question_ids == shuffled.question_ids

    def test_the_key_lands_on_every_position_20_to_30_percent_over_1000_seeds(
        self, ccao_weights, healthy_bank
    ):
        # The authored key is always at index 0 here -- the worst case, which the
        # current bank comes close to.
        opts = _four_options(healthy_bank)
        positions: Counter[int] = Counter()
        for seed in range(1000):
            exam = generate_exam(ccao_weights, healthy_bank, 60, seed=seed, options=opts)
            qid = exam.question_ids[0]
            positions[exam.option_order[qid].index(opts[qid][0])] += 1
        shares = {p: positions[p] / 1000 for p in range(4)}
        assert all(0.20 <= share <= 0.30 for share in shares.values()), shares

    def test_no_option_map_means_no_option_order(self, ccao_weights, healthy_bank):
        assert generate_exam(ccao_weights, healthy_bank, 60, seed=1).option_order == {}


class TestOrderOptions:
    @staticmethod
    def _opts(*ids):
        return [SimpleNamespace(id=i) for i in ids]

    def test_options_follow_the_stored_order(self):
        ordered = order_options(self._opts(1, 2, 3, 4), [3, 1, 4, 2])
        assert [o.id for o in ordered] == [3, 1, 4, 2]

    def test_a_legacy_attempt_with_no_stored_order_keeps_authored_order(self):
        assert [o.id for o in order_options(self._opts(1, 2, 3), None)] == [1, 2, 3]

    def test_an_option_missing_from_the_stored_order_goes_last(self):
        assert [o.id for o in order_options(self._opts(1, 2, 3), [2, 1])] == [2, 1, 3]

    def test_display_labels_are_positional_letters(self):
        assert [display_label(i) for i in range(4)] == ["A", "B", "C", "D"]
