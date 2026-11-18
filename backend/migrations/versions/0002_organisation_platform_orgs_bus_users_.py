"""organisation platform: orgs, BUs, users, decisions, audit

The tables Track H's phase 0 adds (docs/TRACK_H.md), plus the columns that
tie existing jobs, candidates and interviews to an organisation. Every new
column on an existing table is nullable or has a server default, so rows made
by the old /admin panel stay valid: their jobs read as `open`, their
candidates as stage `new`. Foreign keys are named so the downgrade can find
them on Postgres.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18 21:54:46.472741
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('organisations',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('business_units',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('org_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organisations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'name', name='uq_business_units_org_name')
    )
    with op.batch_alter_table('business_units', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_business_units_org_id'), ['org_id'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('org_id', sa.String(length=64), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('bu_id', sa.String(length=64), nullable=True),
    sa.Column('password_hash', sa.String(length=256), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('last_sign_in_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['bu_id'], ['business_units.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organisations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_bu_id'), ['bu_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_org_id'), ['org_id'], unique=False)

    op.create_table('audit_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('org_id', sa.String(length=64), nullable=True),
    sa.Column('actor_user_id', sa.String(length=64), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('subject_type', sa.String(length=32), nullable=True),
    sa.Column('subject_id', sa.String(length=64), nullable=True),
    sa.Column('detail', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organisations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_events_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_events_actor_user_id'), ['actor_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_events_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_events_org_id'), ['org_id'], unique=False)

    op.create_table('decisions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('org_id', sa.String(length=64), nullable=True),
    sa.Column('candidate_id', sa.String(length=64), nullable=False),
    sa.Column('from_stage', sa.String(length=32), nullable=False),
    sa.Column('to_stage', sa.String(length=32), nullable=False),
    sa.Column('actor', sa.String(length=16), nullable=False),
    sa.Column('actor_user_id', sa.String(length=64), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('model_recommendation', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organisations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('decisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_decisions_actor_user_id'), ['actor_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_decisions_candidate_id'), ['candidate_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_decisions_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_decisions_org_id'), ['org_id'], unique=False)

    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('org_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('stage', sa.String(length=32), server_default='new', nullable=False))
        batch_op.create_index(batch_op.f('ix_candidates_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_candidates_stage'), ['stage'], unique=False)
        batch_op.create_foreign_key('fk_candidates_org_id_organisations', 'organisations', ['org_id'], ['id'])

    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.add_column(sa.Column('org_id', sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f('ix_interviews_org_id'), ['org_id'], unique=False)
        batch_op.create_foreign_key('fk_interviews_org_id_organisations', 'organisations', ['org_id'], ['id'])

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('org_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('bu_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('created_by', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('request_status', sa.String(length=16), server_default='open', nullable=False))
        batch_op.create_index(batch_op.f('ix_jobs_bu_id'), ['bu_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_org_id'), ['org_id'], unique=False)
        batch_op.create_foreign_key('fk_jobs_bu_id_business_units', 'business_units', ['bu_id'], ['id'])
        batch_op.create_foreign_key('fk_jobs_created_by_users', 'users', ['created_by'], ['id'])
        batch_op.create_foreign_key('fk_jobs_org_id_organisations', 'organisations', ['org_id'], ['id'])



def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_jobs_org_id_organisations', type_='foreignkey')
        batch_op.drop_constraint('fk_jobs_created_by_users', type_='foreignkey')
        batch_op.drop_constraint('fk_jobs_bu_id_business_units', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_jobs_org_id'))
        batch_op.drop_index(batch_op.f('ix_jobs_bu_id'))
        batch_op.drop_column('request_status')
        batch_op.drop_column('created_by')
        batch_op.drop_column('bu_id')
        batch_op.drop_column('org_id')

    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.drop_constraint('fk_interviews_org_id_organisations', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_interviews_org_id'))
        batch_op.drop_column('org_id')

    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_constraint('fk_candidates_org_id_organisations', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_candidates_stage'))
        batch_op.drop_index(batch_op.f('ix_candidates_org_id'))
        batch_op.drop_column('stage')
        batch_op.drop_column('org_id')

    with op.batch_alter_table('decisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_decisions_org_id'))
        batch_op.drop_index(batch_op.f('ix_decisions_created_at'))
        batch_op.drop_index(batch_op.f('ix_decisions_candidate_id'))
        batch_op.drop_index(batch_op.f('ix_decisions_actor_user_id'))

    op.drop_table('decisions')
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_events_org_id'))
        batch_op.drop_index(batch_op.f('ix_audit_events_created_at'))
        batch_op.drop_index(batch_op.f('ix_audit_events_actor_user_id'))
        batch_op.drop_index(batch_op.f('ix_audit_events_action'))

    op.drop_table('audit_events')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_org_id'))
        batch_op.drop_index(batch_op.f('ix_users_email'))
        batch_op.drop_index(batch_op.f('ix_users_bu_id'))

    op.drop_table('users')
    with op.batch_alter_table('business_units', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_business_units_org_id'))

    op.drop_table('business_units')
    op.drop_table('organisations')
