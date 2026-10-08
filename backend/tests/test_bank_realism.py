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

# Measured 2026-10-08 on the bank at commit 5725c39 (after step 2), before any
# distractor rewrite. Remove a scope's entry once its rewrite passes the gate.
# Rewritten and passing (entry removed): OEV, GRR, WISD.
KNOWN_GAPS = {
    "PTE": "longest=key 13/13 (100%); label B 50%; absolute-word wrong 8/10 (80%)",
    "PMS": "longest=key 12/13 (92%); label B 59%; absolute-word wrong 3/3 (100%)",
    "CKM": "longest=key 13/13 (100%); label A 59%; absolute-word wrong 5/6 (83%)",
    "TRO": "longest=key 12/13 (92%); label A 73%; absolute-word wrong 8/8 (100%)",
    "overall": (
        "longest=key 86/91 (95%); labels A75 B58 C18 D3 (A 49%); "
        "absolute-word wrong 44/51 (86%)"
    ),
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
