"""Blueprint apportionment: turning percentage weights into whole item counts.

A certification blueprint states domain weights as percentages (14%, 21%, ...), but an
exam is made of whole questions. 14% of 60 items is 8.4 questions, and rounding each
domain independently produces 58 or 61 items -- not 60.

This is the classic apportionment problem. We use the largest-remainder (Hamilton)
method, which pins the total to exactly `total_items` while keeping each domain as close
to its exact quota as whole numbers allow.

Weights are integer basis points (1400 == 14.00%) so that a blueprint sums to exactly
10000 with no floating-point drift -- see D-3 in SPEC-CERT-MASTERY.md.
"""

from __future__ import annotations

from dataclasses import dataclass

BPS_TOTAL = 10_000


class BlueprintError(ValueError):
    """Raised when a blueprint is internally inconsistent."""


@dataclass(frozen=True)
class DomainWeight:
    """One domain's share of an exam blueprint.

    `position` is the domain's published order. It is the tie-break key during
    apportionment, which is what makes allocation deterministic across runs.
    """

    code: str
    weight_bps: int
    position: int


def validate_weights(weights: list[DomainWeight]) -> None:
    """Reject a blueprint that cannot produce a valid exam.

    Checked here rather than at the call site so every entry point -- seeding, exam
    generation, the API -- gets the same guarantee.
    """
    if not weights:
        raise BlueprintError("Blueprint has no domains.")

    total = sum(w.weight_bps for w in weights)
    if total != BPS_TOTAL:
        raise BlueprintError(
            f"Domain weights must sum to {BPS_TOTAL} bps (100.00%); got {total} bps "
            f"({total / 100:.2f}%) across {len(weights)} domains."
        )

    if any(w.weight_bps <= 0 for w in weights):
        bad = [w.code for w in weights if w.weight_bps <= 0]
        raise BlueprintError(f"Domain weights must be positive; got <= 0 for {bad}.")

    codes = [w.code for w in weights]
    if len(set(codes)) != len(codes):
        raise BlueprintError(f"Duplicate domain codes in blueprint: {codes}.")


def allocate_items(weights: list[DomainWeight], total_items: int) -> dict[str, int]:
    """Apportion `total_items` across domains by weight, summing to exactly `total_items`.

    Largest-remainder method:
      1. exact quota  q_i = weight_bps_i * n / 10000
      2. every domain receives floor(q_i)
      3. the leftover items go to the largest fractional remainders, ties broken by
         `position` so the outcome is reproducible

    Returns a {domain_code: item_count} mapping.
    """
    validate_weights(weights)
    if total_items < 0:
        raise BlueprintError(f"total_items must be non-negative; got {total_items}.")
    if total_items == 0:
        return {w.code: 0 for w in weights}

    exact = {w.code: w.weight_bps * total_items / BPS_TOTAL for w in weights}
    allocation = {code: int(q) for code, q in exact.items()}

    leftover = total_items - sum(allocation.values())

    # Descending remainder; ascending position as the deterministic tie-break.
    position = {w.code: w.position for w in weights}
    ranked = sorted(
        weights,
        key=lambda w: (-(exact[w.code] - allocation[w.code]), position[w.code]),
    )
    for w in ranked[:leftover]:
        allocation[w.code] += 1

    return allocation


def allocation_summary(
    weights: list[DomainWeight], total_items: int
) -> list[tuple[str, int, float, float]]:
    """Allocation with its deviation from the ideal, for display and diagnostics.

    Returns (code, allocated_items, target_pct, actual_pct) per domain. On a 60-item
    exam a domain can sit up to ~0.8 points off its target percentage; that is inherent
    to whole-number apportionment, not a bug, and this makes it visible.
    """
    allocation = allocate_items(weights, total_items)
    rows = []
    for w in sorted(weights, key=lambda w: w.position):
        target_pct = w.weight_bps / 100
        actual_pct = (allocation[w.code] / total_items * 100) if total_items else 0.0
        rows.append((w.code, allocation[w.code], target_pct, actual_pct))
    return rows
