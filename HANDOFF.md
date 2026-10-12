# Handoff: CCAO-F bank realism

Brief: stop the CCAO-F practice bank giving away its answers through format.
Status as of 2026-10-12: the bank rewrite is complete and the gate passes
for every scope with no xfail. Production rollout is not done (see Remaining).

## Completed (VERIFIED)

Final full run after the KNOWN_GAPS removal, 2026-10-12 (Windows 11, Python 3.14.7,
`python`):

    backend:  847 passed in 205.34s (0:03:25)   (python -m pytest -o addopts="")
    frontend: Tests  32 passed (32)              (npx vitest run)

`-o addopts=""` only strips the `-q` in pytest.ini so the summary line prints;
the plain `python -m pytest` also exits 0 with 847 passes. Previous full run
at the PMS commit, 2026-10-08: backend 840 passed, 3 xfailed. The 3 xfails
(CKM, TRO, overall) now pass; the other 4 new tests are
`test_question_is_well_formed` for CKM-017/018 and TRO-017/018.

| Step | Commit | What |
|---|---|---|
| 1 Shuffle options per attempt | a2770dd | Seeded per-attempt shuffle, re-lettered by position; `attempt_items.option_order` (migration b7d2e4f19a30); review shows presented order; grading by option id (unchanged). |
| 2 "Select N." | 5725c39 | `questions.select_count` (migration c4a8f1e2d903); runner shows "Select N.", caps at N, blocks submit on partial MR items; phrase removed from 21 stems. MR stays all-or-nothing. **Unverified assumption:** the CCAO-F Exam Guide v1.0 was not available, so partial-credit policy was not checked against it (README notes this). |
| 3 Bank lint in CI | 3056e2a | `tests/test_bank_realism.py` + `app/services/bank_realism.py`; `.github/workflows/backend-tests.yml` (repo had no CI; workflow not yet seen running on GitHub). |
| 4 groundwork | d451c7c | Table-format items: `questions.stem_table` (migration d91b3c7a5e24), rendered as a real `<table>`. |
| 4 OEV | 4662514 | Rewritten + 2 table items; passes the gate. Also adds the mirror rule (key uniquely *shortest* >35% fails); not in the original brief. |
| 4 GRR | 8fd6f9b | Rewritten + 2 table items; passes. |
| 4 WISD | 22f8c09 | Rewritten + 2 table items; passes. |
| 4 PTE | bcee726 | Rewritten + 2 table items; passes. |
| 4 PMS | d009cf9 | Rewritten + 2 table items; passes. |
| 4 CKM | d161a7e | Rewritten + 2 table items (CKM-017/018); passes. |
| 4 TRO | 00ddaa8 | Rewritten + 2 table items (TRO-017/018); passes. |
| Gate + README | (this commit) | KNOWN_GAPS emptied (CKM, TRO, overall removed); README "Bank realism" table marked VERIFIED. |

No correct answer was changed in any domain (correct=true stays on the same
option; keys were reworded/extended with meaning kept; MR correct sets and
select_count unchanged). Two key rewordings worth a reviewer's glance:
CKM-014 ("applies to that one request" -> "belongs to that one conversation",
same scope/persistence point, stated more accurately) and TRO-004 ("a change in
the model" -> "... onto a higher rate per token", the direction the stem implies).

Note: the CKM and TRO commits on their own fail `test_bank_realism.py` with
strict XPASS (the brief put the KNOWN_GAPS removal in the final commit); only the
final commit was pushed, and it is green.

## Bank statistics (final)

Command: `cd backend && python -m app.services.bank_realism` (2026-10-12)

    scope    items   mcq  mr table    longest=key   shortest=key   top label   absolute wrong  median
    PTE         18    15   3     2    4/15   27%    3/15   20%   B  25%     6/9    67%     124
    OEV         18    15   3     2    4/15   27%    3/15   20%   B  25%     2/4    50%     170
    PMS         18    15   3     2    5/15   33%    3/15   20%   B  25%     1/4    25%     126
    WISD        18    15   3     2    4/15   27%    3/15   20%   B  25%     4/8    50%     133
    CKM         18    15   3     2    2/15   13%    3/15   20%   B  25%     0/1     0%     131
    GRR         18    15   3     2    4/15   27%    4/15   27%   B  25%     5/8    62%     139
    TRO         18    15   3     2    3/15   20%    4/15   27%   B  25%     0/0     0%     125
    overall    126   105  21    14   26/105  25%   23/105  22%   B  25%    18/34   53%     130
    correct-option labels (overall): {'A': 35, 'B': 42, 'C': 40, 'D': 42, 'E': 9}
    violations: 0

Baseline before any change (112 items): longest=key 86/91 (95%); correct
labels A75 B58 C18 D3; absolute-word options wrong 44/51 (86%); median 58.

These are bank-format statistics only. No claim is made about pass rates
or readiness.

## Remaining (PROPOSED)

1. **Production (needs its own gate):** migrations b7d2e4f19a30,
   c4a8f1e2d903, d91b3c7a5e24 are additive but not applied to Neon. The
   content changes cannot reach production through `seed.py`: it refuses
   to run once attempts exist, and `upsert_question` recreates option rows
   (new ids) under graded attempts. A content-migration plan is needed.
2. Optional: vary MR select counts beyond 3 (all MR items are still
   "Select 3"; that would change correct sets, so flag it explicitly).
3. Minor: CKM/TRO MR options are 88-120 chars, longer than PMS's MR options
   but below the 115-145 band used for single-answer options; PMS was the
   style reference. The realism gate does not measure MR option lengths.
