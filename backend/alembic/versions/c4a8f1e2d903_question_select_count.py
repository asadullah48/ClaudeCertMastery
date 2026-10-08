"""question select count

Revision ID: c4a8f1e2d903
Revises: b7d2e4f19a30
Create Date: 2026-10-08 11:40:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4a8f1e2d903'
down_revision: Union[str, None] = 'b7d2e4f19a30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # "Select N." on multi-response items. Purely additive -- one NULLable integer.
    # Values arrive with content, not here: seed.py sets select_count from the YAML.
    with op.batch_alter_table('questions') as batch_op:
        batch_op.add_column(sa.Column('select_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('questions') as batch_op:
        batch_op.drop_column('select_count')
