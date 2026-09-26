"""learner auth subject

Revision ID: 3e0571713958
Revises: cecda87bf72a
Create Date: 2026-09-26 10:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3e0571713958'
down_revision: Union[str, None] = 'cecda87bf72a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Learner isolation: bind a users row to a verified sign-in. Purely additive --
    # one NULLable column plus a unique index. No row is read, rewritten or
    # backfilled here; linking the founder's existing user is a separate, explicitly
    # authorised step (scripts/link_founder_account.py).
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('auth_subject', sa.String(length=255), nullable=True))
        batch_op.create_index('ix_users_auth_subject', ['auth_subject'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_index('ix_users_auth_subject')
        batch_op.drop_column('auth_subject')
