"""retention periods and deletion requests

The org admin's privacy screen (DPDP Act 2023): how long each kind of
personal data is kept after a candidate's process ends, and the log of
candidates' requests to have their data erased.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('retention_cv_days', sa.Integer(), server_default='180', nullable=False))
        batch_op.add_column(sa.Column('retention_recording_days', sa.Integer(), server_default='30', nullable=False))
        batch_op.add_column(sa.Column('retention_transcript_days', sa.Integer(), server_default='365', nullable=False))

    op.create_table('deletion_requests',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('org_id', sa.String(length=64), nullable=False),
    sa.Column('candidate_id', sa.String(length=64), nullable=True),
    sa.Column('candidate_name', sa.String(length=256), nullable=False),
    sa.Column('contact_note', sa.Text(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('received_by', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='open', nullable=False),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('resolved_by', sa.String(length=64), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name='fk_deletion_requests_candidate', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organisations.id'], name='fk_deletion_requests_org'),
    sa.ForeignKeyConstraint(['received_by'], ['users.id'], name='fk_deletion_requests_received_by'),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], name='fk_deletion_requests_resolved_by'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('deletion_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_deletion_requests_candidate_id'), ['candidate_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_deletion_requests_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_deletion_requests_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('deletion_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_deletion_requests_status'))
        batch_op.drop_index(batch_op.f('ix_deletion_requests_org_id'))
        batch_op.drop_index(batch_op.f('ix_deletion_requests_candidate_id'))
    op.drop_table('deletion_requests')

    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.drop_column('retention_transcript_days')
        batch_op.drop_column('retention_recording_days')
        batch_op.drop_column('retention_cv_days')
