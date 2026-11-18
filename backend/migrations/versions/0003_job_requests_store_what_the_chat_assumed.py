"""job requests: store what the chat assumed

`jobs.spec_inferred`: the assumptions the clarification chat made but the
employer never stated, kept with the spec so the BU head sees them every time
they open "what MITRA understood". Nullable: old jobs have none recorded.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18 22:23:01.932169
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('spec_inferred', sa.JSON(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('spec_inferred')

