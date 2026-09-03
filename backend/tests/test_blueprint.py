"""Blueprint apportionment: totals, fidelity and determinism."""

from __future__ import annotations

import pytest

from app.services.blueprint import (
    BPS_TOTAL,
    BlueprintError,
    DomainWeight,
    allocate_items,
    allocation_summary,
    validate_weights,
)
from tests.conftest import CCAO_F_ITEMS_AT_60


class TestWeightValidation:
    def test_published_ccao_f_weights_sum_to_exactly_100_percent(self, ccao_weights):
        # Integer basis points make this an exact equality with no epsilon (D-3).
        assert sum(w.weight_bps for w in ccao_weights) == BPS_TOTAL

    def test_weights_that_do_not_total_100_percent_are_rejected(self):
        with pytest.raises(BlueprintError, match="must sum to"):
            validate_weights([DomainWeight("A", 5000, 1), DomainWeight("B", 4000, 2)])

    def test_empty_blueprint_is_rejected(self):
        with pytest.raises(BlueprintError, match="no domains"):
            validate_weights([])

    def test_non_positive_weight_is_rejected(self):
        with pytest.raises(BlueprintError, match="positive"):
            validate_weights([DomainWeight("A", 10000, 1), DomainWeight("B", 0, 2)])

    def test_duplicate_domain_codes_are_rejected(self):
        with pytest.raises(BlueprintError, match="Duplicate"):
            validate_weights([DomainWeight("A", 5000, 1), DomainWeight("A", 5000, 2)])


class TestAllocation:
    def test_ccao_f_60_item_split_matches_the_blueprint(self, ccao_weights):
        assert allocate_items(ccao_weights, 60) == CCAO_F_ITEMS_AT_60

    @pytest.mark.parametrize("n", [1, 7, 13, 30, 60, 100, 137, 500])
    def test_allocation_always_totals_exactly_n(self, ccao_weights, n):
        # The whole point of largest-remainder: naive rounding gives 58 or 61 at n=60.
        assert sum(allocate_items(ccao_weights, n).values()) == n

    def test_at_100_items_allocation_reproduces_published_percentages(self, ccao_weights):
        alloc = allocate_items(ccao_weights, 100)
        for w in ccao_weights:
            assert alloc[w.code] == w.weight_bps // 100

    def test_zero_items_allocates_nothing(self, ccao_weights):
        assert allocate_items(ccao_weights, 0) == {w.code: 0 for w in ccao_weights}

    def test_negative_item_count_is_rejected(self, ccao_weights):
        with pytest.raises(BlueprintError):
            allocate_items(ccao_weights, -1)

    def test_heaviest_domain_never_receives_fewer_items_than_the_lightest(self, ccao_weights):
        alloc = allocate_items(ccao_weights, 60)
        assert alloc["OEV"] > alloc["TRO"]  # 21% vs 10%
