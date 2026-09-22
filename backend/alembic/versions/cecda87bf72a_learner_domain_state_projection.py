"""learner domain state projection

Revision ID: cecda87bf72a
Revises: acaf1bf56c8b
Create Date: 2026-09-21 15:49:18.513117
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'cecda87bf72a'
down_revision: Union[str, None] = 'acaf1bf56c8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### KSOR Slice 1: schema only. No evidence is read, no row is backfilled, no
    # existing table is touched -- purely additive, exactly like 28a6724ba01a's own
    # Scenario Lab schema migration. ###
    op.create_table(
        'learner_domain_states',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('track_id', sa.Integer(), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('practice_evidence_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('scenario_evidence_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('distinct_scenario_content_versions', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('recent_practice_mastery_band', sa.String(length=16), nullable=True),
        sa.Column('recent_scenario_mastery_band', sa.String(length=16), nullable=True),
        sa.Column('most_recent_evidence_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('unresolved_misconception_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('readiness_state', sa.String(length=24), nullable=False, server_default='insufficient_evidence'),
        sa.Column('reason_codes', sa.JSON(), nullable=False),
        sa.Column('calculated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('projection_version', sa.Integer(), nullable=False, server_default='1'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['domain_id'], ['domains.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'track_id', 'domain_id', name='uq_learner_domain_state'),
    )
    op.create_index(
        'ix_learner_domain_state_user_track',
        'learner_domain_states',
        ['user_id', 'track_id'],
    )
    # ### end KSOR Slice 1 ###


def downgrade() -> None:
    # Drops only the projection table and its indexes/constraints. No historical
    # evidence table is touched -- this table holds no evidence of its own to lose.
    op.drop_index('ix_learner_domain_state_user_track', table_name='learner_domain_states')
    op.drop_table('learner_domain_states')
