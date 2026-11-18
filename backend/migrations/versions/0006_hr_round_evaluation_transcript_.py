"""hr round evaluation: transcript, attribution and evaluation

The after-call work on an HR round (decision 27): the raw transcript with
speakers told apart, how sure that attribution is, and the evaluation.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19 00:01:48.826624
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('hr_rounds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('eval_status', sa.String(length=24), server_default='not_started', nullable=False))
        batch_op.add_column(sa.Column('eval_error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('transcript', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('attribution', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('evaluation', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('hr_rounds', schema=None) as batch_op:
        batch_op.drop_column('evaluated_at')
        batch_op.drop_column('evaluation')
        batch_op.drop_column('attribution')
        batch_op.drop_column('transcript')
        batch_op.drop_column('eval_error')
        batch_op.drop_column('eval_status')

