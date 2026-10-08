"""question stem table

Revision ID: d91b3c7a5e24
Revises: c4a8f1e2d903
Create Date: 2026-10-08 12:10:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd91b3c7a5e24'
down_revision: Union[str, None] = 'c4a8f1e2d903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Table-format items. Purely additive -- one NULLable JSON column; content arrives
    # through seed.py, not here.
    with op.batch_alter_table('questions') as batch_op:
        batch_op.add_column(sa.Column('stem_table', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('questions') as batch_op:
        batch_op.drop_column('stem_table')
