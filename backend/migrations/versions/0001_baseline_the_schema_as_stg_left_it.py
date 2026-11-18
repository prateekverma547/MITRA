"""baseline: the schema as stg left it

Everything `app.db` defined before the organisation platform: jobs,
clarification turns, candidates and interviews, exactly as `create_all` built
them. A database that existed before migrations (stg, production) is stamped
at this revision by `app.db.prepare_schema` rather than created by it.

Revision ID: 0001
Revises: 
Create Date: 2026-11-18 21:53:47.559148
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('jobs',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=True),
    sa.Column('business_unit', sa.String(length=128), nullable=True),
    sa.Column('source_filename', sa.String(length=512), nullable=True),
    sa.Column('jd_text', sa.Text(), nullable=False),
    sa.Column('evaluation_spec', sa.JSON(), nullable=True),
    sa.Column('spec_status', sa.String(length=32), nullable=False),
    sa.Column('spec_version', sa.Integer(), server_default='1', nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('candidates',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('job_id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=True),
    sa.Column('source_filename', sa.String(length=512), nullable=True),
    sa.Column('cv_text', sa.Text(), nullable=False),
    sa.Column('blueprint', sa.JSON(), nullable=True),
    sa.Column('blueprint_status', sa.String(length=32), nullable=False),
    sa.Column('blueprint_error', sa.Text(), nullable=True),
    sa.Column('blueprint_generated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('blueprint_refinements', sa.JSON(), nullable=True),
    sa.Column('spec_version', sa.Integer(), server_default='1', nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('clarification_turns',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('job_id', sa.String(length=64), nullable=False),
    sa.Column('index', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('interviews',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('candidate_id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('meeting_id', sa.String(length=32), nullable=False),
    sa.Column('password', sa.String(length=64), nullable=False),
    sa.Column('daily_room_name', sa.String(length=128), nullable=False),
    sa.Column('daily_room_url', sa.String(length=512), nullable=False),
    sa.Column('room_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('candidate_timezone', sa.String(length=64), nullable=True),
    sa.Column('transcript', sa.JSON(), nullable=True),
    sa.Column('section_outcomes', sa.JSON(), nullable=True),
    sa.Column('feedback_report', sa.JSON(), nullable=True),
    sa.Column('feedback_status', sa.String(length=32), server_default='pending', nullable=False),
    sa.Column('feedback_error', sa.Text(), nullable=True),
    sa.Column('feedback_generated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('session_metrics', sa.JSON(), nullable=True),
    sa.Column('focus_events', sa.JSON(), nullable=True),
    sa.Column('consent_accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('recording_status', sa.String(length=32), server_default='not_recorded', nullable=False),
    sa.Column('recording_id', sa.String(length=64), nullable=True),
    sa.Column('recording_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('recording_offset_seconds', sa.Float(), nullable=True),
    sa.Column('recording_path', sa.String(length=512), nullable=True),
    sa.Column('recording_bytes', sa.Integer(), nullable=True),
    sa.Column('recording_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('recording_deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('recording_error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_interviews_candidate_id'), ['candidate_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_interviews_meeting_id'), ['meeting_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_interviews_recording_expires_at'), ['recording_expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_interviews_recording_status'), ['recording_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_interviews_status'), ['status'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_interviews_status'))
        batch_op.drop_index(batch_op.f('ix_interviews_recording_status'))
        batch_op.drop_index(batch_op.f('ix_interviews_recording_expires_at'))
        batch_op.drop_index(batch_op.f('ix_interviews_meeting_id'))
        batch_op.drop_index(batch_op.f('ix_interviews_candidate_id'))

    op.drop_table('interviews')
    op.drop_table('clarification_turns')
    op.drop_table('candidates')
    op.drop_table('jobs')
