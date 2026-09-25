"""Deterministic recommendation: weakest-domain selection, position tie-break, and
the no-prior-attempt fallback (plan Section 7.4).

Uses a disposable SQLite DB with a minimal hand-built fixture (not the full CCAO-F
seed) -- only Track/Domain/User/ExamAttempt/AttemptDomainScore rows are needed to
exercise this deterministic logic.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AttemptDomainScore,
    AttemptStatus,
    Domain,
    ExamAttempt,
    LearnerDomainState,
    Track,
    User,
)
from app.services.readiness_policy import (  # noqa: E402
    DOMAIN_BELOW_THRESHOLD,
    INSUFFICIENT_DOMAIN_COVERAGE,
    INSUFFICIENT_PRACTICE_EVIDENCE,
    MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY,
    NO_APPLIED_SCENARIO_EVIDENCE,
    NO_EVIDENCE,
    REPEATED_MISCONCEPTION,
    REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE,
    STALE_EVIDENCE,
    STATE_APPROACHING_READY,
    STATE_DEVELOPING,
    STATE_INSUFFICIENT_EVIDENCE,
    STATE_READY,
)
from app.services.scenario_recommender import (  # noqa: E402
    ACTION_ATTEMPT_SCENARIO,
    ACTION_COLLECT_PRACTICE_EVIDENCE,
    ACTION_PROCEED_TO_READINESS_EVALUATION,
    ACTION_REASSESS_DOMAIN,
    ACTION_REMEDIATE_MISCONCEPTION,
    DomainReadinessCandidate,
    ScenarioRecommenderError,
    recommend_domain_code,
    recommend_next_action,
    recommend_next_action_from_candidates,
)


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scenario_rec.db'}")
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    yield session
    session.close()


@pytest.fixture
def track_with_domains(db_session):
    """PTE(pos1)/OEV(pos2)/WISD(pos3), matching the real CCAO-F ordering convention."""
    track = Track(code="CCAO-F", name="AI Operator, Foundation")
    db_session.add(track)
    db_session.flush()
    domains = {
        "PTE": Domain(track_id=track.id, code="PTE", name="Prompting", weight_bps=1400, position=1),
        "OEV": Domain(track_id=track.id, code="OEV", name="Output Eval", weight_bps=2100, position=2),
        "WISD": Domain(track_id=track.id, code="WISD", name="Workflow", weight_bps=1600, position=3),
    }
    db_session.add_all(domains.values())
    db_session.flush()
    user = User(email="dev@certmastery.local", display_name="Dev")
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    return track, domains, user


def _submitted_attempt(db_session, track, user, domain_scores: dict[str, float], domains):
    attempt = ExamAttempt(
        user_id=user.id, track_id=track.id, status=AttemptStatus.SUBMITTED,
        seed=1, submitted_at=datetime.now(timezone.utc),
    )
    db_session.add(attempt)
    db_session.flush()
    for code, pct in domain_scores.items():
        db_session.add(
            AttemptDomainScore(
                attempt_id=attempt.id, domain_id=domains[code].id,
                correct=int(pct / 10), total=10, percentage=pct,
                mastery_band="developing",
            )
        )
    db_session.commit()
    return attempt


class TestNoPriorAttemptFallback:
    def test_falls_back_to_position_one_domain(self, db_session, track_with_domains):
        track, domains, user = track_with_domains
        code = recommend_domain_code(db_session, user_id=user.id, track_code="CCAO-F")
        assert code == "PTE"  # position 1, stated deterministic default

    def test_unknown_track_raises(self, db_session, track_with_domains):
        with pytest.raises(ScenarioRecommenderError):
            recommend_domain_code(db_session, user_id=1, track_code="NOPE")


class TestWeakestDomainSelection:
    def test_recommends_the_lowest_scoring_domain(self, db_session, track_with_domains):
        track, domains, user = track_with_domains
        _submitted_attempt(
            db_session, track, user,
            {"PTE": 90.0, "OEV": 40.0, "WISD": 70.0}, domains,
        )
        code = recommend_domain_code(db_session, user_id=user.id, track_code="CCAO-F")
        assert code == "OEV"

    def test_only_the_most_recent_submitted_attempt_is_used(
        self, db_session, track_with_domains
    ):
        track, domains, user = track_with_domains
        _submitted_attempt(db_session, track, user, {"PTE": 20.0}, domains)
        _submitted_attempt(db_session, track, user, {"OEV": 20.0}, domains)
        # The second (most recent) attempt only scored OEV, so OEV is the only
        # candidate even though the earlier attempt's PTE score was also low.
        code = recommend_domain_code(db_session, user_id=user.id, track_code="CCAO-F")
        assert code == "OEV"


class TestPositionTieBreak:
    def test_equal_percentage_breaks_tie_by_domain_position_ascending(
        self, db_session, track_with_domains
    ):
        track, domains, user = track_with_domains
        _submitted_attempt(
            db_session, track, user,
            {"OEV": 50.0, "WISD": 50.0, "PTE": 90.0}, domains,
        )
        # OEV (position 2) and WISD (position 3) tie at 50%; OEV wins the tie-break.
        code = recommend_domain_code(db_session, user_id=user.id, track_code="CCAO-F")
        assert code == "OEV"


class TestNoModelCallAnywhere:
    def test_recommender_module_has_no_ai_or_mcp_import(self):
        import inspect

        from app.services import scenario_recommender

        source = inspect.getsource(scenario_recommender)
        for forbidden in ("anthropic", "mcp", "zia_client"):
            assert forbidden not in source.lower()


# --- C3 Slice 5: recommend_next_action() -- pure ranking core -----------------------
#
# candidate() builds a DomainReadinessCandidate directly (no DB) so the priority-tier
# logic itself is tested without touching SQLAlchemy at all, matching
# test_readiness_policy.py's own pure-function style.


def candidate(
    code, position, *, state=STATE_READY, reasons=(), practice=10, scenario=5, misconceptions=0
):
    return DomainReadinessCandidate(
        domain_code=code, domain_position=position, readiness_state=state,
        reason_codes=tuple(reasons), practice_evidence_count=practice,
        scenario_evidence_count=scenario, unresolved_misconception_count=misconceptions,
    )


class TestRecommendNextActionCore:
    def test_no_evidence_recommends_collect_practice(self):
        c = candidate("PTE", 1, state=STATE_INSUFFICIENT_EVIDENCE, reasons=[NO_EVIDENCE],
                       practice=0, scenario=0)
        result = recommend_next_action_from_candidates([c])
        assert result.action == ACTION_COLLECT_PRACTICE_EVIDENCE
        assert result.domain_code == "PTE"

    def test_one_domain_insufficient_others_stronger_prioritizes_insufficient(self):
        ready = candidate("PTE", 1, state=STATE_READY)
        insufficient = candidate("OEV", 2, state=STATE_INSUFFICIENT_EVIDENCE,
                                  reasons=[NO_EVIDENCE], practice=0, scenario=0)
        developing = candidate("WISD", 3, state=STATE_DEVELOPING, reasons=[DOMAIN_BELOW_THRESHOLD])
        result = recommend_next_action_from_candidates([ready, insufficient, developing])
        assert result.domain_code == "OEV"
        assert result.action == ACTION_COLLECT_PRACTICE_EVIDENCE

    def test_missing_practice_reason_reflects_evidence_gap(self):
        c = candidate("PTE", 1, state=STATE_INSUFFICIENT_EVIDENCE,
                       reasons=[INSUFFICIENT_PRACTICE_EVIDENCE], practice=2, scenario=3)
        result = recommend_next_action_from_candidates([c])
        assert INSUFFICIENT_PRACTICE_EVIDENCE in result.reason_codes
        assert DOMAIN_BELOW_THRESHOLD not in result.reason_codes

    def test_missing_applied_scenario_recommends_attempt_scenario(self):
        c = candidate("PTE", 1, state=STATE_INSUFFICIENT_EVIDENCE,
                       reasons=[NO_APPLIED_SCENARIO_EVIDENCE],
                       practice=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY, scenario=0)
        result = recommend_next_action_from_candidates([c])
        assert result.action == ACTION_ATTEMPT_SCENARIO
        assert NO_APPLIED_SCENARIO_EVIDENCE in result.reason_codes

    def test_repeated_non_diverse_scenario_preserves_evidence_quality_distinction(self):
        # Projection v3 shape (Gate C3-C2): repeats of one scenario leave the domain
        # insufficient_evidence, with the repeat cap named alongside the sufficiency
        # reason. The raw scenario count (3) is still what the ranking compares.
        c = candidate("PTE", 1, state=STATE_INSUFFICIENT_EVIDENCE,
                       reasons=[NO_APPLIED_SCENARIO_EVIDENCE,
                                REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE],
                       practice=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY, scenario=3)
        result = recommend_next_action_from_candidates([c])
        assert result.action == ACTION_ATTEMPT_SCENARIO
        assert REPEATED_SCENARIO_NOT_DIVERSE_EVIDENCE in result.reason_codes

    def test_current_production_shape_still_recommends_oev_scenario(self):
        """Gate C3-C2 regression: today's founder projections (PTE one scenario,
        OEV-TRO none, all practice-sufficient, all insufficient_evidence) must still
        yield ATTEMPT_SCENARIO on OEV under projection v3."""
        practice = {"PTE": 67, "OEV": 111, "PMS": 60, "WISD": 89, "CKM": 60, "GRR": 81, "TRO": 52}
        cands = [
            candidate(code, i + 1, state=STATE_INSUFFICIENT_EVIDENCE,
                      reasons=[NO_APPLIED_SCENARIO_EVIDENCE], practice=p,
                      scenario=1 if code == "PTE" else 0)
            for i, (code, p) in enumerate(practice.items())
        ]
        result = recommend_next_action_from_candidates(cands)
        assert (result.action, result.domain_code) == (ACTION_ATTEMPT_SCENARIO, "OEV")

    def test_unresolved_misconception_outranks_plain_developing(self):
        plain = candidate("PTE", 1, state=STATE_DEVELOPING, reasons=[DOMAIN_BELOW_THRESHOLD])
        flagged = candidate("OEV", 2, state=STATE_DEVELOPING, reasons=[REPEATED_MISCONCEPTION],
                             misconceptions=1)
        result = recommend_next_action_from_candidates([plain, flagged])
        assert result.domain_code == "OEV"
        assert result.action == ACTION_REMEDIATE_MISCONCEPTION

    def test_weak_mastery_represented_as_weakness_not_missing_evidence(self):
        c = candidate("PTE", 1, state=STATE_DEVELOPING, reasons=[DOMAIN_BELOW_THRESHOLD])
        result = recommend_next_action_from_candidates([c])
        assert result.reason_codes == (DOMAIN_BELOW_THRESHOLD,)
        assert NO_EVIDENCE not in result.reason_codes

    def test_stale_evidence_recommends_reassessment(self):
        stale = candidate("PTE", 1, state=STATE_APPROACHING_READY, reasons=[STALE_EVIDENCE])
        result = recommend_next_action_from_candidates([stale])
        assert result.action == ACTION_REASSESS_DOMAIN
        assert result.domain_code == "PTE"

    def test_ready_domain_does_not_outrank_genuine_gap(self):
        ready = candidate("PTE", 1, state=STATE_READY)
        gap = candidate("OEV", 2, state=STATE_INSUFFICIENT_EVIDENCE, reasons=[NO_EVIDENCE],
                         practice=0, scenario=0)
        result = recommend_next_action_from_candidates([ready, gap])
        assert result.domain_code == "OEV"

    def test_all_ready_proceeds_to_readiness_evaluation(self):
        result = recommend_next_action_from_candidates([
            candidate("PTE", 1, state=STATE_READY),
            candidate("OEV", 2, state=STATE_READY),
            candidate("WISD", 3, state=STATE_READY),
        ])
        assert result.action == ACTION_PROCEED_TO_READINESS_EVALUATION
        assert result.domain_code is None

    def test_stable_tie_break_by_domain_position(self):
        a = candidate("OEV", 2, state=STATE_INSUFFICIENT_EVIDENCE, reasons=[NO_EVIDENCE],
                       practice=0, scenario=0)
        b = candidate("WISD", 3, state=STATE_INSUFFICIENT_EVIDENCE, reasons=[NO_EVIDENCE],
                       practice=0, scenario=0)
        r1 = recommend_next_action_from_candidates([a, b])
        r2 = recommend_next_action_from_candidates([b, a])
        assert r1 == r2
        assert r1.domain_code == "OEV"

    def test_deterministic_repeatability(self):
        candidates = [
            candidate("PTE", 1, state=STATE_READY),
            candidate("OEV", 2, state=STATE_DEVELOPING, reasons=[DOMAIN_BELOW_THRESHOLD]),
        ]
        first = recommend_next_action_from_candidates(candidates)
        for _ in range(5):
            assert recommend_next_action_from_candidates(candidates) == first


# --- C3 Slice 5: recommend_next_action() -- ORM adapter ------------------------------


def _state_row(
    db, user, track, domain, *, state, reasons=(), practice=0, scenario=0, distinct=0,
    misconceptions=0,
):
    row = LearnerDomainState(
        user_id=user.id, track_id=track.id, domain_id=domain.id,
        practice_evidence_count=practice, scenario_evidence_count=scenario,
        distinct_scenario_content_versions=distinct,
        unresolved_misconception_count=misconceptions,
        readiness_state=state, reason_codes=list(reasons),
    )
    db.add(row)
    db.commit()
    return row


class TestRecommendNextActionAdapter:
    def test_missing_projection_regression(self, db_session, track_with_domains):
        """PTE has a materialized row; OEV/WISD have none at all -- the missing rows
        must not be silently dropped from the candidate set."""
        track, domains, user = track_with_domains
        _state_row(
            db_session, user, track, domains["PTE"], state=STATE_READY,
            practice=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY, scenario=5, distinct=5,
        )
        result = recommend_next_action(db_session, user_id=user.id, track_code="CCAO-F")
        assert result.action == ACTION_COLLECT_PRACTICE_EVIDENCE
        assert result.domain_code == "OEV"  # position 2 -- the first missing-row domain
        assert INSUFFICIENT_DOMAIN_COVERAGE in result.reason_codes

    def test_production_shaped_regression(self, db_session, track_with_domains):
        """Mirrors the real production shape: PTE has one strong scenario and zero
        practice (insufficient_evidence); OEV/WISD have no projection at all."""
        track, domains, user = track_with_domains
        _state_row(
            db_session, user, track, domains["PTE"], state=STATE_INSUFFICIENT_EVIDENCE,
            reasons=[INSUFFICIENT_PRACTICE_EVIDENCE, NO_APPLIED_SCENARIO_EVIDENCE],
            practice=0, scenario=1, distinct=1,
        )
        result = recommend_next_action(db_session, user_id=user.id, track_code="CCAO-F")
        assert result.action == ACTION_COLLECT_PRACTICE_EVIDENCE
        # Must never claim demonstrated weakness for an evidence gap.
        assert DOMAIN_BELOW_THRESHOLD not in result.reason_codes

    def test_user_isolation(self, db_session, track_with_domains):
        track, domains, user = track_with_domains
        other = User(email="other@example.com", display_name="Other")
        db_session.add(other)
        db_session.commit()
        for code in ("PTE", "OEV", "WISD"):
            _state_row(
                db_session, user, track, domains[code], state=STATE_READY,
                practice=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY, scenario=5, distinct=5,
            )
        result = recommend_next_action(db_session, user_id=other.id, track_code="CCAO-F")
        assert result.action == ACTION_COLLECT_PRACTICE_EVIDENCE

    def test_track_isolation(self, db_session, track_with_domains):
        track, domains, user = track_with_domains
        other_track = Track(code="CCDV-F", name="Other Track")
        db_session.add(other_track)
        db_session.flush()
        other_domain = Domain(
            track_id=other_track.id, code="PTE", name="Prompting", weight_bps=1000, position=1
        )
        db_session.add(other_domain)
        db_session.commit()
        _state_row(
            db_session, user, other_track, other_domain, state=STATE_DEVELOPING,
            reasons=[DOMAIN_BELOW_THRESHOLD], practice=10, scenario=5,
        )
        result = recommend_next_action(db_session, user_id=user.id, track_code="CCAO-F")
        assert result.action == ACTION_COLLECT_PRACTICE_EVIDENCE

    def test_unknown_track_raises(self, db_session, track_with_domains):
        with pytest.raises(ScenarioRecommenderError):
            recommend_next_action(db_session, user_id=1, track_code="NOPE")

    def test_does_not_mutate_learner_domain_state(self, db_session, track_with_domains):
        track, domains, user = track_with_domains
        row = _state_row(db_session, user, track, domains["PTE"], state=STATE_READY)
        before = (row.readiness_state, list(row.reason_codes), row.practice_evidence_count)
        recommend_next_action(db_session, user_id=user.id, track_code="CCAO-F")
        db_session.expire_all()
        reloaded = db_session.get(LearnerDomainState, row.id)
        after = (reloaded.readiness_state, list(reloaded.reason_codes), reloaded.practice_evidence_count)
        assert after == before


# --- Scenario-level recommendation (retake validity) --------------------------------

from dataclasses import replace as _replace  # noqa: E402

from app.models import Scenario, ScenarioAttempt  # noqa: E402
from app.services.scenario_recommender import unexposed_scenarios_by_domain  # noqa: E402


def _with_unseen(c, *scenarios):
    return _replace(c, unexposed_scenarios=tuple(scenarios))


class TestScenarioLevelRecommendation:
    def test_attempt_scenario_names_the_first_unseen_scenario(self):
        c = _with_unseen(
            candidate("OEV", 2, state=STATE_INSUFFICIENT_EVIDENCE, reasons=[NO_APPLIED_SCENARIO_EVIDENCE],
                      practice=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY, scenario=0),
            "CCAO-F-OEV-SCN-001", "CCAO-F-OEV-SCN-002")
        result = recommend_next_action_from_candidates([c])
        assert (result.action, result.domain_code, result.scenario_external_id) == (
            ACTION_ATTEMPT_SCENARIO, "OEV", "CCAO-F-OEV-SCN-001")

    def test_second_independent_scenario_is_recommended_after_the_first(self):
        """PTE has one fresh result (001 seen); v3 sufficiency needs a second distinct
        scenario, so the recommendation is the unseen 002 -- never a 001 repeat."""
        c = _with_unseen(
            candidate("PTE", 1, state=STATE_INSUFFICIENT_EVIDENCE, reasons=[NO_APPLIED_SCENARIO_EVIDENCE],
                      practice=67, scenario=1),
            "CCAO-F-PTE-SCN-002")
        result = recommend_next_action_from_candidates([c])
        assert (result.action, result.scenario_external_id) == (ACTION_ATTEMPT_SCENARIO, "CCAO-F-PTE-SCN-002")

    def test_domain_whose_scenarios_are_all_seen_falls_back_to_practice(self):
        c = _with_unseen(
            candidate("PTE", 1, state=STATE_DEVELOPING, reasons=[DOMAIN_BELOW_THRESHOLD],
                      practice=67, scenario=2))
        result = recommend_next_action_from_candidates([c])
        assert (result.action, result.domain_code, result.scenario_external_id) == (
            ACTION_COLLECT_PRACTICE_EVIDENCE, "PTE", None)

    def test_scenario_tier_skips_a_domain_with_nothing_unseen(self):
        seen = _with_unseen(candidate("PTE", 1, state=STATE_INSUFFICIENT_EVIDENCE,
                                      reasons=[NO_APPLIED_SCENARIO_EVIDENCE],
                                      practice=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY, scenario=0))
        open_ = _with_unseen(candidate("OEV", 2, state=STATE_INSUFFICIENT_EVIDENCE,
                                       reasons=[NO_APPLIED_SCENARIO_EVIDENCE],
                                       practice=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY, scenario=0),
                             "CCAO-F-OEV-SCN-001")
        result = recommend_next_action_from_candidates([seen, open_])
        assert (result.domain_code, result.scenario_external_id) == ("OEV", "CCAO-F-OEV-SCN-001")

    def test_unsupplied_scenario_list_keeps_domain_level_behaviour(self):
        c = candidate("OEV", 2, state=STATE_INSUFFICIENT_EVIDENCE, reasons=[NO_APPLIED_SCENARIO_EVIDENCE],
                      practice=MIN_PRACTICE_ITEMS_FOR_SUFFICIENCY, scenario=0)
        result = recommend_next_action_from_candidates([c])
        assert (result.action, result.scenario_external_id) == (ACTION_ATTEMPT_SCENARIO, None)


def _scenario(db, domain, external_id, active=True):
    s = Scenario(domain_id=domain.id, external_id=external_id, title=external_id,
                 setup_text="s", difficulty=2, is_active=active, content_version=1)
    db.add(s)
    db.commit()
    return s


class TestUnexposedScenarioLookup:
    def test_seen_and_inactive_scenarios_are_excluded(self, db_session, track_with_domains):
        track, domains, user = track_with_domains
        pte1 = _scenario(db_session, domains["PTE"], "CCAO-F-PTE-SCN-001")
        _scenario(db_session, domains["PTE"], "CCAO-F-PTE-SCN-002")
        _scenario(db_session, domains["OEV"], "CCAO-F-OEV-SCN-001")
        _scenario(db_session, domains["WISD"], "CCAO-F-WISD-SCN-001", active=False)
        db_session.add(ScenarioAttempt(user_id=user.id, scenario_id=pte1.id, status="submitted",
                                       scenario_content_version=1, submitted_at=datetime.now(timezone.utc),
                                       score_pct=100.0, mastery_band="strong"))
        db_session.commit()
        by_domain = unexposed_scenarios_by_domain(db_session, user_id=user.id, track_id=track.id)
        assert by_domain == {
            domains["PTE"].id: ("CCAO-F-PTE-SCN-002",),
            domains["OEV"].id: ("CCAO-F-OEV-SCN-001",),
        }

    def test_adapter_recommends_a_specific_unseen_scenario(self, db_session, track_with_domains):
        track, domains, user = track_with_domains
        for code in ("PTE", "OEV", "WISD"):
            _scenario(db_session, domains[code], f"CCAO-F-{code}-SCN-001")
        pte = db_session.query(Scenario).filter_by(external_id="CCAO-F-PTE-SCN-001").one()
        db_session.add(ScenarioAttempt(user_id=user.id, scenario_id=pte.id, status="submitted",
                                       scenario_content_version=1, submitted_at=datetime.now(timezone.utc),
                                       score_pct=100.0, mastery_band="strong"))
        db_session.commit()
        _state_row(db_session, user, track, domains["PTE"], state=STATE_INSUFFICIENT_EVIDENCE,
                   reasons=[NO_APPLIED_SCENARIO_EVIDENCE], practice=30, scenario=1, distinct=1)
        for code in ("OEV", "WISD"):
            _state_row(db_session, user, track, domains[code], state=STATE_INSUFFICIENT_EVIDENCE,
                       reasons=[NO_APPLIED_SCENARIO_EVIDENCE], practice=30, scenario=0)
        result = recommend_next_action(db_session, user_id=user.id, track_code="CCAO-F")
        assert (result.action, result.domain_code, result.scenario_external_id) == (
            ACTION_ATTEMPT_SCENARIO, "OEV", "CCAO-F-OEV-SCN-001")
