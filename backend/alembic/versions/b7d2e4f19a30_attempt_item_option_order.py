"""attempt item option order

Revision ID: b7d2e4f19a30
Revises: 6bb0aa76744f
Create Date: 2026-10-08 11:20:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d2e4f19a30'
down_revision: Union[str, None] = '6bb0aa76744f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Per-attempt option shuffling. Purely additive -- one NULLable JSON column, no
    # backfill. Existing attempts keep NULL, which reads as "presented in authored
    # order", the order they were in fact shown in.
    with op.batch_alter_table('attempt_items') as batch_op:
        batch_op.add_column(sa.Column('option_order', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('attempt_items') as batch_op:
        batch_op.drop_column('option_order')
