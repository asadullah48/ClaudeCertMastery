"""Integrity of the authored question bank.

These run against the YAML source rather than the database, so a malformed question is
caught in CI even if nobody has re-seeded. A question with no correct option would
otherwise reach candidates as an item that cannot be answered correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SEED_DIR = Path(__file__).resolve().parents[1] / "seed_data" / "ccao_f"
MIN_QUESTIONS_PER_DOMAIN = 15

DOMAIN_FILES = sorted(SEED_DIR.glob("*.yaml"))


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


ALL_DOMAINS = [load(p) for p in DOMAIN_FILES]
ALL_QUESTIONS = [(d["domain"]["code"], q) for d in ALL_DOMAINS for q in d["questions"]]


class TestBankStructure:
    def test_all_seven_domain_files_are_present(self):
        assert len(DOMAIN_FILES) == 7

    def test_weights_sum_to_exactly_100_percent(self):
        assert sum(d["domain"]["weight_bps"] for d in ALL_DOMAINS) == 10_000

    def test_domain_positions_are_unique_and_contiguous(self):
        positions = sorted(d["domain"]["position"] for d in ALL_DOMAINS)
        assert positions == list(range(1, 8))

    def test_domain_codes_are_unique(self):
        codes = [d["domain"]["code"] for d in ALL_DOMAINS]
        assert len(set(codes)) == len(codes)

    @pytest.mark.parametrize("domain", ALL_DOMAINS, ids=lambda d: d["domain"]["code"])
    def test_each_domain_meets_the_minimum_question_count(self, domain):
        assert len(domain["questions"]) >= MIN_QUESTIONS_PER_DOMAIN

    def test_bank_is_large_enough_for_a_full_60_item_exam(self):
        # Every domain must independently cover its own 60-item quota, or generation
        # falls back to redistribution and the exam stops being blueprint-faithful.
        from app.services.blueprint import allocate_items
        from tests.conftest import CCAO_F_WEIGHTS

        quota = allocate_items(list(CCAO_F_WEIGHTS), 60)
        for domain in ALL_DOMAINS:
            code = domain["domain"]["code"]
            assert len(domain["questions"]) >= quota[code]

    def test_external_ids_are_globally_unique(self):
        ids = [q["external_id"] for _, q in ALL_QUESTIONS]
        assert len(set(ids)) == len(ids)


class TestQuestionValidity:
    @pytest.mark.parametrize(
        "code,q", ALL_QUESTIONS, ids=[q["external_id"] for _, q in ALL_QUESTIONS]
    )
    def test_question_is_well_formed(self, code, q):
        qid = q["external_id"]
        assert q["type"] in ("mcq", "mr"), f"{qid}: unknown type"
        assert q["stem"].strip(), f"{qid}: empty stem"
        assert q.get("explanation", "").strip(), f"{qid}: missing explanation"
        assert len(q["options"]) >= 3, f"{qid}: too few options"

        correct = [o for o in q["options"] if o.get("correct")]
        if q["type"] == "mcq":
            assert len(correct) == 1, f"{qid}: MCQ needs exactly 1 correct option"
        else:
            assert len(correct) >= 2, f"{qid}: MR needs at least 2 correct options"

        assert len(correct) < len(q["options"]), f"{qid}: every option marked correct"

        labels = [o["label"] for o in q["options"]]
        assert len(set(labels)) == len(labels), f"{qid}: duplicate option labels"
        assert all(o["text"].strip() for o in q["options"]), f"{qid}: empty option text"
        assert 1 <= q.get("difficulty", 2) <= 3, f"{qid}: difficulty out of range"

    def test_external_ids_follow_the_naming_convention(self):
        for code, q in ALL_QUESTIONS:
            assert q["external_id"].startswith(f"CCAO-F-{code}-"), q["external_id"]

    def test_every_domain_includes_at_least_one_multi_response_item(self):
        # A bank of only MCQ items would never exercise the MR grading path that
        # candidates meet on the real exam.
        for domain in ALL_DOMAINS:
            types = {q["type"] for q in domain["questions"]}
            assert "mr" in types, f"{domain['domain']['code']} has no MR items"

    def test_difficulty_is_not_uniform_across_the_bank(self):
        difficulties = {q.get("difficulty", 2) for _, q in ALL_QUESTIONS}
        assert len(difficulties) > 1


class TestSelectCount:
    """MR items state how many to select; the stem never says 'select all that apply'."""

    @pytest.mark.parametrize(
        "code,q",
        [(c, q) for c, q in ALL_QUESTIONS if q["type"] == "mr"],
        ids=lambda v: v["external_id"] if isinstance(v, dict) else v,
    )
    def test_mr_select_count_equals_its_correct_options(self, code, q):
        assert q.get("select_count") == sum(1 for o in q["options"] if o.get("correct"))

    def test_mcq_items_carry_no_select_count(self):
        assert not [q["external_id"] for _, q in ALL_QUESTIONS
                    if q["type"] == "mcq" and "select_count" in q]

    def test_no_stem_says_select_all_that_apply(self):
        assert not [q["external_id"] for _, q in ALL_QUESTIONS
                    if "select all that apply" in q["stem"].lower()]

    def test_the_seed_rejects_a_mismatched_select_count(self):
        import seed

        bad = {
            "external_id": "CCAO-F-OEV-999", "type": "mr", "select_count": 3,
            "stem": "Which?", "explanation": "Because.",
            "options": [
                {"label": "A", "text": "a", "correct": True},
                {"label": "B", "text": "b", "correct": True},
                {"label": "C", "text": "c", "correct": False},
            ],
        }
        with pytest.raises(ValueError, match="select_count"):
            seed.validate_bank("OEV", [bad])


class TestTableItems:
    def test_every_authored_table_is_rectangular(self):
        for _, q in ALL_QUESTIONS:
            if "table" in q:
                cols = q["table"]["columns"]
                assert len(cols) >= 2 and len(q["table"]["rows"]) >= 2, q["external_id"]
                assert all(len(r) == len(cols) for r in q["table"]["rows"]), q["external_id"]

    def test_the_seed_rejects_a_ragged_table(self):
        import seed

        bad = {
            "external_id": "CCAO-F-OEV-998", "type": "mcq", "stem": "Which row?",
            "explanation": "Because.",
            "table": {"columns": ["Plan", "Reviewer"], "rows": [["1", "x"], ["2"]]},
            "options": [
                {"label": "A", "text": "a", "correct": True},
                {"label": "B", "text": "b", "correct": False},
                {"label": "C", "text": "c", "correct": False},
            ],
        }
        with pytest.raises(ValueError, match="table row"):
            seed.validate_bank("OEV", [bad])
