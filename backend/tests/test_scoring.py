"""Scaled scoring: anchors, monotonicity, rounding and the pass boundary."""

from __future__ import annotations

import pytest

from app.services.scoring import (
    DEFAULT_PASS_RAW,
    PASS_SCALED,
    SCALE_MAX,
    SCALE_MIN,
    ScoringError,
    scale_score,
)


class TestAnchors:
    """The three anchor points must be exact, not approximate."""

    def test_zero_raw_maps_to_scale_minimum(self):
        assert scale_score(0, 60) == SCALE_MIN

    def test_perfect_raw_maps_to_scale_maximum(self):
        assert scale_score(60, 60) == SCALE_MAX

    def test_pass_threshold_maps_exactly_to_pass_line(self):
        # 42/60 is exactly 70%, so this lands on the anchor with no rounding involved.
        assert scale_score(42, 60) == PASS_SCALED

    @pytest.mark.parametrize(
        "correct,expected", [(0, 100), (30, 543), (41, 705), (42, 720), (45, 767), (54, 907), (60, 1000)]
    )
    def test_published_reference_values(self, correct, expected):
        assert scale_score(correct, 60) == expected


class TestPassBoundary:
    """scaled >= 720 must hold exactly when raw >= pass_raw."""

    def test_one_item_below_threshold_fails(self):
        assert scale_score(41, 60) < PASS_SCALED

    def test_exactly_at_threshold_passes(self):
        assert scale_score(42, 60) >= PASS_SCALED

    @pytest.mark.parametrize("total", [60, 100, 137, 500])
    def test_iff_property_holds_across_every_raw_score(self, total):
        # The guarantee that makes the pass line meaningful. Rounding must never move a
        # score across it in either direction.
        for correct in range(total + 1):
            scaled = scale_score(correct, total)
            raw = correct / total
            assert (scaled >= PASS_SCALED) == (raw >= DEFAULT_PASS_RAW), (
                f"{correct}/{total} raw={raw:.6f} scaled={scaled}"
            )

    def test_rounding_cannot_promote_a_near_miss_to_a_pass(self):
        # 13990/20000 = 69.95% -> formula gives 719.55, which naive rounding would turn
        # into a reported pass the candidate did not earn.
        assert scale_score(13990, 20000) == PASS_SCALED - 1

    def test_just_below_threshold_at_high_resolution_still_fails(self):
        assert scale_score(13999, 20000) < PASS_SCALED

    def test_exactly_threshold_at_high_resolution_passes(self):
        assert scale_score(14000, 20000) == PASS_SCALED


class TestMonotonicity:
    @pytest.mark.parametrize("total", [60, 200])
    def test_more_correct_never_lowers_the_score(self, total):
        scores = [scale_score(c, total) for c in range(total + 1)]
        assert all(a <= b for a, b in zip(scores, scores[1:]))

    def test_score_is_strictly_bounded_by_the_scale(self, ):
        for c in range(0, 61):
            assert SCALE_MIN <= scale_score(c, 60) <= SCALE_MAX


class TestConfigurableThreshold:
    def test_alternate_threshold_still_anchors_the_pass_line(self):
        # A track needing 80% raw must still report exactly 720 at that point.
        assert scale_score(80, 100, pass_raw=0.80) == PASS_SCALED

    def test_alternate_threshold_shifts_the_boundary(self):
        assert scale_score(75, 100, pass_raw=0.80) < PASS_SCALED
        assert scale_score(75, 100, pass_raw=0.70) > PASS_SCALED

    def test_endpoints_are_invariant_to_the_threshold(self):
        for pr in (0.5, 0.6, 0.7, 0.8):
            assert scale_score(0, 50, pass_raw=pr) == SCALE_MIN
            assert scale_score(50, 50, pass_raw=pr) == SCALE_MAX


class TestValidation:
    def test_zero_total_is_rejected(self):
        with pytest.raises(ScoringError):
            scale_score(0, 0)

    def test_negative_total_is_rejected(self):
        with pytest.raises(ScoringError):
            scale_score(0, -5)

    def test_correct_exceeding_total_is_rejected(self):
        with pytest.raises(ScoringError):
            scale_score(61, 60)

    def test_negative_correct_is_rejected(self):
        with pytest.raises(ScoringError):
            scale_score(-1, 60)

    @pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
    def test_out_of_range_threshold_is_rejected(self, bad):
        with pytest.raises(ScoringError):
            scale_score(30, 60, pass_raw=bad)
