"""Bank realism gate: the authored CCAO-F bank must not give its answers away by format.

Fails a scope (each domain, and the bank overall) if any of these hold:

  - the key is the uniquely longest option in more than 35% of single-answer items;
  - the key is the uniquely shortest option in more than 35% of single-answer items
    (the mirror rule -- not in the original brief; it stops a rewrite that fixes the
    longest-option tell from simply inverting it);
  - any authored label holds more than 35% of correct options (pre-shuffle content --
    the runtime shuffle hides this from candidates, but a bank that needs the shuffle
    to be fair is one export or one fallback away from leaking again);
  - options containing always / never / only / all are wrong more than 75% of the time;
  - in more than 35% of multi-response items the correct set is exactly the N shortest
    or the N longest options (every correct option strictly shorter, or strictly longer,
    than every wrong one), so "pick the N shortest" scores full marks;
  - any multi-response item has no select_count.

Deterministic: reads the YAML only. The same measurement prints as a table with

    python -m app.services.bank_realism

Scopes still failing are marked xfail(strict=True) with their measured numbers, so the
gap is visible in every run and a scope that gets fixed without its mark being removed
turns the suite red.
"""

from __future__ import annotations

import pytest

from app.services.bank_realism import measure

OVERALL, PER_DOMAIN = measure()
SCOPES = {s.scope: s for s in [*PER_DOMAIN, OVERALL]}

# First measured 2026-10-08 on the bank at commit 5725c39 (after step 2), before any
# distractor rewrite. A scope's entry was removed once its rewrite passed the gate.
# Rewritten and passing (entry removed): OEV, GRR, WISD, PTE, PMS, CKM, TRO, overall.
# Add an entry here only for a scope that is known to fail and is being worked on.
#
# MR length rule, measured 2026-10-12 at commit 5d2b854 before the MR rewrite.
# PTE, CKM and TRO pass at 1/3 (33%). Remove an entry once its MR rewrite passes.
KNOWN_GAPS: dict[str, str] = {
    "WISD": "MR correct set length-ordered 2/3 (67%)",
    "GRR": "MR correct set length-ordered 2/3 (67%)",
}


def _param(scope: str):
    marks = []
    if scope in KNOWN_GAPS:
        marks.append(
            pytest.mark.xfail(strict=True, reason=f"{scope}: {KNOWN_GAPS[scope]}")
        )
    return pytest.param(scope, marks=marks, id=scope)


@pytest.mark.parametrize("scope", [_param(s) for s in SCOPES])
def test_bank_does_not_leak_answers_through_format(scope):
    problems = SCOPES[scope].violations()
    assert not problems, "\n".join(problems)


def test_every_known_gap_names_a_real_scope():
    # A typo in KNOWN_GAPS would silently leave a failing scope unmarked -- or a fixed
    # one marked forever.
    assert set(KNOWN_GAPS) <= set(SCOPES)


def test_the_lint_detects_each_leak_on_synthetic_content(tmp_path):
    """The gate itself must work: one item exhibiting every leak trips every rule."""
    (tmp_path / "x.yaml").write_text(
        """
domain: {code: X}
questions:
  - external_id: X-1
    type: mcq
    stem: s
    options:
      - {label: A, text: "The correct and noticeably longest option of the four here", correct: true}
      - {label: B, text: "Always wrong", correct: false}
      - {label: C, text: "Never right", correct: false}
      - {label: D, text: "Short", correct: false}
  - external_id: X-2
    type: mr
    stem: s
    options:
      - {label: A, text: "a", correct: true}
      - {label: B, text: "b", correct: true}
      - {label: C, text: "c", correct: false}
""",
        encoding="utf-8",
    )
    overall, _ = measure(tmp_path)
    text = "\n".join(overall.violations())
    assert "uniquely longest" in text
    assert "label A" in text
    assert "absolute-word" in text
    assert "without select_count: X-2" in text


def test_the_lint_detects_length_ordered_mr_sets_on_synthetic_content(tmp_path):
    """MR rule: a correct set that is exactly the shortest (or longest) options fires;
    one whose lengths overlap the wrong options does not."""
    leaky, fair = tmp_path / "leaky", tmp_path / "fair"
    leaky.mkdir()
    fair.mkdir()
    (leaky / "x.yaml").write_text(
        """
domain: {code: X}
questions:
  - external_id: X-1
    type: mr
    select_count: 2
    stem: s
    options:
      - {label: A, text: "short", correct: true}
      - {label: B, text: "brief", correct: true}
      - {label: C, text: "a much longer wrong option", correct: false}
      - {label: D, text: "another long wrong option", correct: false}
  - external_id: X-2
    type: mr
    select_count: 2
    stem: s
    options:
      - {label: B, text: "a much longer right option", correct: true}
      - {label: C, text: "another long right option", correct: true}
      - {label: A, text: "short", correct: false}
      - {label: D, text: "brief", correct: false}
""",
        encoding="utf-8",
    )
    (fair / "x.yaml").write_text(
        """
domain: {code: Y}
questions:
  - external_id: Y-1
    type: mr
    select_count: 2
    stem: s
    options:
      - {label: A, text: "short", correct: true}
      - {label: B, text: "a much longer right option", correct: true}
      - {label: C, text: "brief", correct: false}
      - {label: D, text: "another long wrong option", correct: false}
""",
        encoding="utf-8",
    )
    leaky_overall, _ = measure(leaky)
    assert leaky_overall.mr_set_by_length == 2
    assert "MR correct set is exactly the shortest or longest" in "\n".join(
        leaky_overall.violations()
    )
    fair_overall, _ = measure(fair)
    assert fair_overall.mr_set_by_length == 0
    assert "MR correct set" not in "\n".join(fair_overall.violations())
