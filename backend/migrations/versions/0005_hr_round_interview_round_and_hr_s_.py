"""hr round: interview round and HR's working record

`interviews.round` says whether MITRA (`technical`) or a person (`hr`) runs the
room; every existing interview was technical. `hr_rounds` holds what HR asked
and noted, in Track H's own table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18 23:28:08.811411
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('hr_rounds',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('interview_id', sa.String(length=64), nullable=False),
    sa.Column('candidate_id', sa.String(length=64), nullable=False),
    sa.Column('booked_by', sa.String(length=64), nullable=True),
    sa.Column('questions_asked', sa.JSON(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('hr_joined_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['booked_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('hr_rounds', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hr_rounds_candidate_id'), ['candidate_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hr_rounds_interview_id'), ['interview_id'], unique=True)

    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.add_column(sa.Column('round', sa.String(length=16), server_default='technical', nullable=False))



def downgrade() -> None:
    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.drop_column('round')

    with op.batch_alter_table('hr_rounds', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_hr_rounds_interview_id'))
        batch_op.drop_index(batch_op.f('ix_hr_rounds_candidate_id'))

    op.drop_table('hr_rounds')
