"""screenings: fit analysis and HR questions per candidate

Track H phase 1, step 2. One row per candidate, in its own table so screening
never has to alter the shared `candidates` table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18 22:56:10.918800
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('screenings',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('candidate_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='waiting', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('fit_analysis', sa.JSON(), nullable=True),
    sa.Column('hr_questions', sa.JSON(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('screenings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_screenings_candidate_id'), ['candidate_id'], unique=True)



def downgrade() -> None:
    with op.batch_alter_table('screenings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_screenings_candidate_id'))

    op.drop_table('screenings')
