"""Domain-weighted exam generation.

Composes an exam whose domain mix matches the published blueprint, drawing items from
the available question bank. Kept free of any database dependency -- callers pass in a
plain {domain_code: [question_id, ...]} bank -- so generation is testable without
fixtures and reusable for practice sets and domain drills.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence, TypeVar

from app.services.blueprint import DomainWeight, allocate_items

T = TypeVar("T")

# Display letters for a presented option list. Authored labels are not used for display:
# they encode where the author happened to put the key, which is exactly the leak that
# per-attempt shuffling exists to remove.
DISPLAY_LABELS = "ABCDEFGH"


@dataclass(frozen=True)
class GeneratedExam:
    """A composed exam: which questions, in what order, and how it was assembled."""

    seed: int
    question_ids: list[int]
    per_domain: dict[str, int]
    requested_items: int
    composition_warning: str | None = None
    domain_of_question: dict[int, str] = field(default_factory=dict)
    # question_id -> option ids in the order this attempt presents them. Empty for
    # callers that pass no option map (pure composition tests).
    option_order: dict[int, list[int]] = field(default_factory=dict)

    @property
    def item_count(self) -> int:
        return len(self.question_ids)

    @property
    def is_complete(self) -> bool:
        return self.item_count == self.requested_items


class ExamGenerationError(ValueError):
    """Raised when no valid exam can be composed from the available bank."""


def display_label(index: int) -> str:
    """The letter shown for the option at ``index`` in a presented order."""
    return DISPLAY_LABELS[index]


def order_options(options: Sequence[T], order: Sequence[int] | None) -> list[T]:
    """Arrange option objects (anything with an ``id``) in a stored presentation order.

    ``order`` of None means a legacy attempt recorded before shuffling existed: those
    were shown in authored order, so that is what a faithful review reproduces. Options
    missing from ``order`` (added to the bank after the attempt) go last, in authored
    order, rather than vanishing.
    """
    if not order:
        return list(options)
    rank = {oid: i for i, oid in enumerate(order)}
    return sorted(options, key=lambda o: rank.get(o.id, len(rank)))


def _redistribute(
    weights: list[DomainWeight],
    quota: dict[str, int],
    available: dict[str, int],
) -> tuple[dict[str, int], list[str]]:
    """Move items from under-supplied domains to domains that have spare questions.

    Returns the adjusted per-domain counts and a list of human-readable notes describing
    every deviation from the blueprint.

    The alternative -- silently returning a 54-item "60-item exam" -- would quietly
    distort the score, since raw_total would shrink along with it. Better to keep the
    item count honest and say exactly where the mix drifted.
    """
    taken = {c: min(quota[c], available.get(c, 0)) for c in quota}
    notes = [
        f"{c}: wanted {quota[c]}, bank holds {available.get(c, 0)}"
        for c in sorted(quota)
        if taken[c] < quota[c]
    ]

    deficit = sum(quota.values()) - sum(taken.values())
    if deficit == 0:
        return taken, notes

    # Heavier domains absorb the shortfall first, so the mix stays as close to the
    # blueprint as the bank allows. Sorting makes the outcome deterministic.
    order = sorted(weights, key=lambda w: (-w.weight_bps, w.position))
    while deficit > 0:
        spare = [w for w in order if available.get(w.code, 0) - taken[w.code] > 0]
        if not spare:
            break
        for w in spare:
            if deficit == 0:
                break
            taken[w.code] += 1
            deficit -= 1

    if deficit > 0:
        notes.append(f"bank exhausted; exam is {deficit} item(s) short")

    return taken, notes


def generate_exam(
    weights: list[DomainWeight],
    bank: dict[str, list[int]],
    total_items: int,
    seed: int | None = None,
    shuffle: bool = True,
    options: dict[int, list[int]] | None = None,
) -> GeneratedExam:
    """Compose a blueprint-weighted exam.

    Args:
        weights:     the track blueprint (must sum to 10000 bps).
        bank:        {domain_code: [question_id, ...]} of active, eligible questions.
        total_items: how many items the exam should contain.
        seed:        RNG seed. Omitted means a fresh random seed, which is returned on
                     the result so the exam can be reproduced exactly later.
        shuffle:     interleave domains in the final ordering. False keeps items grouped
                     by domain, which is what domain-drill mode wants.
        options:     {question_id: [option_id, ...]} in authored order. When given, each
                     selected item's options are shuffled and returned as option_order.

    Draws each domain's quota without replacement, so no question appears twice.
    """
    if total_items <= 0:
        raise ExamGenerationError(f"total_items must be positive; got {total_items}.")

    if seed is None:
        seed = random.randrange(2**31)
    rng = random.Random(seed)

    quota = allocate_items(weights, total_items)
    available = {w.code: len(bank.get(w.code, [])) for w in weights}

    if sum(available.values()) == 0:
        raise ExamGenerationError(
            "Question bank is empty for every domain in this blueprint."
        )

    per_domain, notes = _redistribute(weights, quota, available)

    selected: list[int] = []
    domain_of: dict[int, str] = {}
    for w in sorted(weights, key=lambda w: w.position):
        pool = list(bank.get(w.code, []))
        take = per_domain[w.code]
        if take <= 0:
            continue
        # sample() draws without replacement, so an item cannot repeat within a domain;
        # pools are disjoint across domains, so it cannot repeat across them either.
        picked = rng.sample(pool, take)
        selected.extend(picked)
        for qid in picked:
            domain_of[qid] = w.code

    if shuffle:
        rng.shuffle(selected)

    # Options are shuffled last, from the same RNG, so adding this step left the
    # question draw for every existing seed unchanged. Always shuffled, even in
    # drill mode: the position of the key is never information the candidate gets.
    option_order: dict[int, list[int]] = {}
    if options is not None:
        for qid in selected:
            order = list(options.get(qid, []))
            rng.shuffle(order)
            option_order[qid] = order

    warning = None
    if notes:
        warning = (
            "Exam composition deviates from the blueprint - "
            + "; ".join(notes)
            + ". Domain percentages on this attempt are not blueprint-faithful."
        )

    return GeneratedExam(
        seed=seed,
        question_ids=selected,
        per_domain=per_domain,
        requested_items=total_items,
        composition_warning=warning,
        domain_of_question=domain_of,
        option_order=option_order,
    )
