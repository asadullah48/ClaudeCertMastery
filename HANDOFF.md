# Handoff: CCAO-F bank realism (paused until after 11 Oct 2026)

Brief: stop the CCAO-F practice bank giving away its answers through format.
Four steps. Status as of 2026-10-08.

## Completed (VERIFIED)

Final full run at the PMS commit, 2026-10-08:

    backend:  840 passed, 3 xfailed, 1 warning in 395.21s   (python -m pytest)
    frontend: Tests 32 passed (32)                          (npx vitest run)

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
| 4 PMS | (this commit) | Rewritten + 2 table items; passes. |

No correct answer was changed in any domain (correct=true stays on the same
option; keys were reworded/extended with meaning kept).

## Bank statistics measured so far

Command: `cd backend && python -m app.services.bank_realism`

    scope    items   mcq  mr table    longest=key   shortest=key   top label   absolute wrong  median
    PTE         18    15   3     2    4/15   27%    3/15   20%   B  25%     6/9    67%     124
    OEV         18    15   3     2    4/15   27%    3/15   20%   B  25%     2/4    50%     170
    PMS         18    15   3     2    5/15   33%    3/15   20%   B  25%     1/4    25%     126
    WISD        18    15   3     2    4/15   27%    3/15   20%   B  25%     4/8    50%     133
    CKM         16    13   3     0   13/13  100%    0/13    0%   A  59%     5/6    83%      52
    GRR         18    15   3     2    4/15   27%    4/15   27%   B  25%     5/8    62%     139
    TRO         16    13   3     0   12/13   92%    0/13    0%   A  73%     8/8   100%      50
    overall    122   101  21    10   46/101  46%   16/101  16%   A  33%    31/47   66%     124

Baseline before any change (112 items): longest=key 86/91 (95%); correct
labels A75 B58 C18 D3; absolute-word options wrong 44/51 (86%); median 58.
(The brief said 45/52; this regex finds 44/51.)

These are bank-format statistics only. No claim is made about pass rates
or readiness.

## Remaining (PROPOSED)

1. Rewrite CKM (`05_ckm.yaml`) and TRO (`07_tro.yaml`) the same way: 2-4
   sentence options, one wrong clause per distractor, labels balanced,
   2 table items each, explanations extended. Check with
   `python -m app.services.bank_realism` after each edit.
2. Remove the CKM, TRO and `overall` entries from `KNOWN_GAPS` in
   `tests/test_bank_realism.py`; the gate must then pass with no xfail.
3. README: replace the old stats with the final table, marked VERIFIED
   with the command above.
4. **Production (needs its own gate):** migrations b7d2e4f19a30,
   c4a8f1e2d903, d91b3c7a5e24 are additive but not applied to Neon. The
   content changes cannot reach production through `seed.py`: it refuses
   to run once attempts exist, and `upsert_question` recreates option rows
   (new ids) under graded attempts. A content-migration plan is needed.
5. Optional: vary MR select counts beyond 3 (all MR items are still
   "Select 3"; that would change correct sets, so flag it explicitly).
