"""learner entitlements

Revision ID: 6bb0aa76744f
Revises: 3e0571713958
Create Date: 2026-09-27 16:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6bb0aa76744f'
down_revision: Union[str, None] = '3e0571713958'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Commercial entitlement (Readiness Pass grants, each scoped to one track via
    # track_code -> tracks.code). Purely additive: one new table,
    # created empty. No existing table is altered and no row is read or written -- every
    # learner is on the free Explorer plan until a grant row exists for them.
    op.create_table(
        'learner_entitlements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('plan', sa.String(length=40), nullable=False),
        sa.Column('track_code', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('provider', sa.String(length=40), nullable=True),
        sa.Column('external_reference', sa.String(length=255), nullable=True),
        sa.Column('granted_by', sa.String(length=120), nullable=True),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['track_code'], ['tracks.code']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_reference'),
    )
    op.create_index(
        'ix_learner_entitlements_user_track_expires',
        'learner_entitlements',
        ['user_id', 'track_code', 'expires_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_learner_entitlements_user_track_expires', table_name='learner_entitlements')
    op.drop_table('learner_entitlements')
