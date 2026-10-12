# Handoff: CCAO-F bank realism

Brief: stop the CCAO-F practice bank giving away its answers through format.
Status as of 2026-10-12: the bank rewrite is complete, including the MR
option-length rewrite, and the gate passes for every scope with no xfail.
Production rollout is not done (see Remaining).

## Completed (VERIFIED)

Final full run after the MR length rewrite, 2026-10-12 (Windows 11, Python 3.14.7,
`python`):

    backend:  848 passed in 366.00s (0:06:06)   (python -m pytest -o addopts="")
    frontend: Tests  32 passed (32)              (npx vitest run)

`-o addopts=""` only strips the `-q` in pytest.ini so the summary line prints.
848 = the 847 of the previous run (5d2b854) + the new synthetic MR-rule test.
Earlier runs: 5d2b854, 847 passed; PMS commit (2026-10-08), 840 passed, 3 xfailed.

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
| Gate + README | 5d2b854 | KNOWN_GAPS emptied (CKM, TRO, overall removed); README "Bank realism" table marked VERIFIED. |
| MR gate | de5a8f8 | New rule: MR correct set exactly the N shortest or N longest options (strict), max 35% per scope; column `mr set=length`. Measured at 5d2b854: overall 12/21 (57%), all with the correct options shortest; OEV, PMS, WISD, GRR, overall failed (strict xfail). |
| MR PTE | d6f1307 | PTE-004/008/013 rewritten. |
| MR OEV | 15abb55 | OEV-004/008/012 rewritten; OEV gap removed. |
| MR PMS | d48094f | PMS-004/008/014 rewritten; PMS and overall gaps removed (overall 6/21 at this point). |
| MR WISD | 72868c3 | WISD-004/008/012 rewritten; WISD gap removed. |
| MR CKM | c25b29d | CKM-004 distractor A shortened; CKM-008/012 already compliant, unchanged. |
| MR GRR | 416e1ed | GRR-008/014 rewritten (GRR-008's wrong option no longer says "never"); GRR-004 unchanged; KNOWN_GAPS empty. |
| MR TRO | 0bfe15d | TRO-015 rewritten; TRO-004/008 unchanged. |
| MR README + handoff | (this commit) | README table with the new column, VERIFIED; this file. |

MR rewrite: every MR option is now 70-130 chars and, in all 21 MR items, at
least one correct option is longer than a wrong one and vice versa. Checked
against 5d2b854 with a script: all 126 items keep identical correct sets,
select_count, type and option labels; 56 option texts changed, all in MR
items; no stem or explanation changed (each explanation's reference to a
distractor was read and still matches). Each commit de5a8f8..0bfe15d was checked
out in a worktree and `tests/test_bank_realism.py` passes on it (no fail, no XPASS).

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

    scope    items   mcq  mr table    longest=key   shortest=key   top label   absolute wrong  mr set=length  median
    PTE         18    15   3     2    4/15   27%    3/15   20%   B  25%     6/9    67%    0/3     0%     124
    OEV         18    15   3     2    4/15   27%    3/15   20%   B  25%     2/4    50%    0/3     0%     170
    PMS         18    15   3     2    5/15   33%    3/15   20%   B  25%     1/4    25%    0/3     0%     126
    WISD        18    15   3     2    4/15   27%    3/15   20%   B  25%     4/8    50%    0/3     0%     133
    CKM         18    15   3     2    2/15   13%    3/15   20%   B  25%     0/1     0%    0/3     0%     131
    GRR         18    15   3     2    4/15   27%    4/15   27%   B  25%     4/7    57%    0/3     0%     139
    TRO         18    15   3     2    3/15   20%    4/15   27%   B  25%     0/0     0%    0/3     0%     125
    overall    126   105  21    14   26/105  25%   23/105  22%   B  25%    17/33   52%    0/21    0%     130
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
   Related: five MR items have 4 options with 3 correct (PTE-008, OEV-008,
   PMS-008, WISD-008, GRR-008), so a candidate only has to spot one wrong
   option. A fifth wrong option would fix that without touching correct sets.

## Line endings

`core.autocrlf=true`. Git stores these files as LF (checked: every file touched
between 5d2b854 and de5a8f8..0bfe15d, in each of those commits). Working
copies are CRLF where git checked them out and LF where a tool rewrote the
file; each file is internally consistent and commits normalise to LF. An
earlier note here that the working tree was all LF was wrong: Git Bash's
`$(printf '\r')` does not give grep a usable CR pattern. Count with Python
(`Path(p).read_bytes().count(b'\r\n')`) instead.
