"""Track I's interview data: live events, proctoring, rejoin, capacity

Track I's tables (live/models.py, proctoring/models.py) and the columns its
interview and plan code writes on `interviews` and `candidates`. Track I made
them with `create_all`; this is their migration. A database that has already
run Track I's code has some or all of them, so every table and column is only
created when it is missing.

`org_id` stays nullable, as Track I left it: interviews booked in the old
/admin panel have no organisation.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19 20:48:03.666561
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())

    if 'capacity_events' not in tables:
        op.create_table('capacity_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('interview_id', sa.String(length=64), nullable=True),
        sa.Column('live_bots', sa.Integer(), nullable=False),
        sa.Column('limit', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('capacity_events', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_capacity_events_at'), ['at'], unique=False)

    if 'connection_signals' not in tables:
        op.create_table('connection_signals',
        sa.Column('seq', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('interview_id', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('detected_by', sa.String(length=32), nullable=False),
        sa.Column('at_seconds', sa.Float(), nullable=False),
        sa.Column('gap_seconds', sa.Float(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('seq')
        )
        with op.batch_alter_table('connection_signals', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_connection_signals_interview_id'), ['interview_id'], unique=False)

    if 'integrity_accommodations' not in tables:
        op.create_table('integrity_accommodations',
        sa.Column('interview_id', sa.String(length=64), nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=True),
        sa.Column('gaze_reminders_off', sa.Boolean(), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('set_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('interview_id')
        )

    if 'integrity_events' not in tables:
        op.create_table('integrity_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=True),
        sa.Column('interview_id', sa.String(length=64), nullable=False),
        sa.Column('event_id', sa.String(length=128), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('start_seconds', sa.Float(), nullable=False),
        sa.Column('end_seconds', sa.Float(), nullable=True),
        sa.Column('event', sa.JSON(), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('interview_id', 'event_id', name='uq_integrity_events_interview_event')
        )
        with op.batch_alter_table('integrity_events', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_integrity_events_interview_id'), ['interview_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_integrity_events_org_id'), ['org_id'], unique=False)

    if 'integrity_reports' not in tables:
        op.create_table('integrity_reports',
        sa.Column('interview_id', sa.String(length=64), nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('built_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('report', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('interview_id')
        )

    if 'interview_events' not in tables:
        op.create_table('interview_events',
        sa.Column('seq', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.String(length=128), nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=True),
        sa.Column('interview_id', sa.String(length=64), nullable=False),
        sa.Column('at_seconds', sa.Float(), nullable=False),
        sa.Column('wall_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('stored_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('seq'),
        sa.UniqueConstraint('event_id')
        )
        with op.batch_alter_table('interview_events', schema=None) as batch_op:
            batch_op.create_index('ix_interview_events_interview_seq', ['interview_id', 'seq'], unique=False)
            batch_op.create_index(batch_op.f('ix_interview_events_org_id'), ['org_id'], unique=False)

    if 'recording_views' not in tables:
        op.create_table('recording_views',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=True),
        sa.Column('interview_id', sa.String(length=64), nullable=False),
        sa.Column('viewer', sa.String(length=64), nullable=False),
        sa.Column('viewed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('byte_range', sa.String(length=64), nullable=True),
        sa.Column('route', sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('recording_views', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_recording_views_interview_id'), ['interview_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_recording_views_org_id'), ['org_id'], unique=False)

    if 'rejoin_passes' not in tables:
        op.create_table('rejoin_passes',
        sa.Column('pass_id', sa.String(length=40), nullable=False),
        sa.Column('interview_id', sa.String(length=64), nullable=False),
        sa.Column('candidate_id', sa.String(length=64), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('pass_id')
        )
        with op.batch_alter_table('rejoin_passes', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_rejoin_passes_interview_id'), ['interview_id'], unique=False)

    if 'watch_records' not in tables:
        op.create_table('watch_records',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=True),
        sa.Column('interview_id', sa.String(length=64), nullable=False),
        sa.Column('watcher', sa.String(length=64), nullable=False),
        sa.Column('daily_user_id', sa.String(length=36), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('watch_records', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_watch_records_interview_id'), ['interview_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_watch_records_org_id'), ['org_id'], unique=False)

    if 'watcher_notes' not in tables:
        op.create_table('watcher_notes',
        sa.Column('note_id', sa.String(length=40), nullable=False),
        sa.Column('org_id', sa.String(length=64), nullable=True),
        sa.Column('interview_id', sa.String(length=64), nullable=False),
        sa.Column('author', sa.String(length=64), nullable=False),
        sa.Column('text', sa.String(length=2000), nullable=False),
        sa.Column('at_seconds', sa.Float(), nullable=False),
        sa.Column('at_estimate', sa.Boolean(), nullable=False),
        sa.Column('area_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('note_id')
        )
        with op.batch_alter_table('watcher_notes', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_watcher_notes_interview_id'), ['interview_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_watcher_notes_org_id'), ['org_id'], unique=False)

    with op.batch_alter_table('candidates', schema=None) as batch_op:
        have = _columns('candidates')
        if 'text_model_calls' not in have:
            batch_op.add_column(sa.Column('text_model_calls', sa.JSON(), nullable=True))

    with op.batch_alter_table('interviews', schema=None) as batch_op:
        have = _columns('interviews')
        if 'recording_download_failures' not in have:
            batch_op.add_column(sa.Column('recording_download_failures', sa.Integer(), nullable=True))
        if 'bot_pid' not in have:
            batch_op.add_column(sa.Column('bot_pid', sa.Integer(), nullable=True))
        if 'recording_collect_by' not in have:
            batch_op.add_column(sa.Column('recording_collect_by', sa.String(length=32), nullable=True))
        if 'recording_collect_until' not in have:
            batch_op.add_column(sa.Column('recording_collect_until', sa.DateTime(timezone=True), nullable=True))
        if 'booked_duration_minutes' not in have:
            batch_op.add_column(sa.Column('booked_duration_minutes', sa.Integer(), nullable=True))
        if 'planned_duration_minutes' not in have:
            batch_op.add_column(sa.Column('planned_duration_minutes', sa.Integer(), nullable=True))
        if 'planned_grace_minutes' not in have:
            batch_op.add_column(sa.Column('planned_grace_minutes', sa.Integer(), nullable=True))
        if 'transcript_zero_at' not in have:
            batch_op.add_column(sa.Column('transcript_zero_at', sa.DateTime(timezone=True), nullable=True))
        if 'cleaned_transcript' not in have:
            batch_op.add_column(sa.Column('cleaned_transcript', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.drop_column('cleaned_transcript')
        batch_op.drop_column('transcript_zero_at')
        batch_op.drop_column('planned_grace_minutes')
        batch_op.drop_column('planned_duration_minutes')
        batch_op.drop_column('booked_duration_minutes')
        batch_op.drop_column('recording_collect_until')
        batch_op.drop_column('recording_collect_by')
        batch_op.drop_column('bot_pid')
        batch_op.drop_column('recording_download_failures')

    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_column('text_model_calls')

    with op.batch_alter_table('watcher_notes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_watcher_notes_org_id'))
        batch_op.drop_index(batch_op.f('ix_watcher_notes_interview_id'))

    op.drop_table('watcher_notes')
    with op.batch_alter_table('watch_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_watch_records_org_id'))
        batch_op.drop_index(batch_op.f('ix_watch_records_interview_id'))

    op.drop_table('watch_records')
    with op.batch_alter_table('rejoin_passes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rejoin_passes_interview_id'))

    op.drop_table('rejoin_passes')
    with op.batch_alter_table('recording_views', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_recording_views_org_id'))
        batch_op.drop_index(batch_op.f('ix_recording_views_interview_id'))

    op.drop_table('recording_views')
    with op.batch_alter_table('interview_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_interview_events_org_id'))
        batch_op.drop_index('ix_interview_events_interview_seq')

    op.drop_table('interview_events')
    op.drop_table('integrity_reports')
    with op.batch_alter_table('integrity_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_integrity_events_org_id'))
        batch_op.drop_index(batch_op.f('ix_integrity_events_interview_id'))

    op.drop_table('integrity_events')
    op.drop_table('integrity_accommodations')
    with op.batch_alter_table('connection_signals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_connection_signals_interview_id'))

    op.drop_table('connection_signals')
    with op.batch_alter_table('capacity_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_capacity_events_at'))

    op.drop_table('capacity_events')
