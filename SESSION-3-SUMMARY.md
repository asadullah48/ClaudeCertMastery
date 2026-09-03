# Session 3 &mdash; Ask Zia widened to all four tracks

**Date:** 2026-09-03
**Outcome:** Delivered. 305 tests passing (18 new).

---

## What changed

Session 2 scoped Ask Zia to CCAR-F/CCAR-P on the assumption that the Agent Factory
curriculum was agentic-AI-specific. The PCAO-F Study Guide shows otherwise: it maps every
CCAO-F domain to a crash course. The panel is now driven entirely by the mapping table,
so it renders on any track with a mapped concept and hides for any question without one.

The Claude API explanation engine remains the default everywhere. Zia is still a
companion, never a replacement &mdash; asserted by a test.

---

## Coverage table

### CCAO-F &mdash; 7/7 domains mapped

| Domain | Lesson | Confidence |
|---|---|---|
| PTE Prompting & Task Execution | `ai-prompting-2026` | 0.95 |
| OEV Output Evaluation & Validation | `problem-solving-crash-course` §verification | 0.90 |
| PMS Product & Model Selection | `claude-chatgpt-101-crash-course` | 0.90 |
| WISD Workflow Integration & Solution Design | `workflow-design-diagnosis-crash-course` | 0.95 |
| CKM Configuration & Knowledge Management | `skills-connectors-crash-course` | 0.90 |
| GRR Governance, Risk & Responsible Use | `governance-risk-responsible-use-crash-course` | 0.95 |
| TRO Troubleshooting & Optimization | `workflow-design-diagnosis-crash-course` | 0.85 |

All 112 authored CCAO-F questions resolve through their domain code &mdash; **no
re-tagging of the bank was needed**.

OEV is the one domain absent from the published Study Guide. It was mapped by search onto
Principle 3, which covers verification as a workflow step and why self-checking by the
same model is not verification. That is the exact distinction the domain tests, so the
fit is better than a title-based guess would have produced.

### CCDV-F &mdash; 6/7 objectives mapped

| Objective | Lesson | Confidence |
|---|---|---|
| Messages API and the request loop | `loop-by-hand-crash-course` | 0.95 |
| Python for AI development | `python-crash-course` | 0.95 |
| Managed agents and hosted harnesses | `claude-managed-agents-crash-course` | 0.95 |
| Agentic AI fundamentals | `claude-agent-sdk-crash-course` | 0.90 |
| Streaming and batch processing | `structured-extraction-crash-course` | 0.85 |
| Tool schema design | `connector-native-apps` | 0.85 |
| **TypeScript SDK development** | **none** | **explicit gap** |

`typescript-sdk` is recorded with `is_mapped=false` and surfaced in the `unmapped` field.
The corpus teaches this track in Python; mapping it onto the Python course would send a
candidate somewhere that does not answer their question.

### CCAR-F / CCAR-P &mdash; unchanged from Session 2

8 concepts, all mapped.

**Totals: 22 concept tags across 4 tracks, 21 mapped, 1 explicit gap.**

---

## How it works now

`GET /api/zia/concepts?track_code=` returns a track's mapped concepts plus its recorded
gaps. The frontend renders from that list alone; the hardcoded `ZIA_CONCEPTS` gate is
gone. Widening coverage to a new track is now a row in `concept_curriculum_map` with no
frontend edit (D-14).

Reused unchanged from Session 2, as the brief required: identity mapping, evidence
logging, transport and auth handling.

---

## Tests

18 new, 305 total. Notably:

- every CCAO-F domain and CCDV-F objective resolves
- sampled real questions resolve via `matched_by == "domain"`, proving no re-tagging
- the unmapped objective hides the panel *and* is reported in `unmapped`
- every concept the `/concepts` endpoint advertises actually resolves, so the panel never
  offers a button that then hides itself
- built-in explanations still serve every item

One Session 2 test was **inverted rather than deleted**:
`test_ccao_questions_have_no_zia_mapping_in_session_2` became
`test_ccao_questions_gained_a_mapping_in_session_3`, so the change of intent is visible
in history rather than silently disappearing.

---

## Still outstanding

- **OAuth against `auth.panaversity.org`.** The endpoint is an OAuth 2.0 protected
  resource; until a token can be obtained, the panel hides itself everywhere. All the
  machinery behind it is tested against a mocked client.
- **Exam runner UI** and the **Claude explanation engine**, both from the original
  Session 2 brief.
- **CCAR-F / CCAR-P question banks**, which is what turns those two tracks from mapped
  concepts into something a candidate meets on a review screen.
