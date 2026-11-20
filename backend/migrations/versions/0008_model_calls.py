"""model calls

One row per call to a model, for the org admin's AI calls trace view and the
cost of each stage. Metadata only: no prompts, CVs or transcripts.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('model_calls',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('org_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('purpose', sa.String(length=128), nullable=False),
    sa.Column('provider', sa.String(length=32), server_default='openai', nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('prompt_version', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='ok', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cached_input_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('reasoning_tokens', sa.Integer(), nullable=True),
    sa.Column('audio_seconds', sa.Float(), nullable=True),
    sa.Column('cost_usd', sa.Float(), nullable=True),
    sa.Column('price_known', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('job_id', sa.String(length=64), nullable=True),
    sa.Column('candidate_id', sa.String(length=64), nullable=True),
    sa.Column('interview_id', sa.String(length=64), nullable=True),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('output_preview', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('model_calls', schema=None) as batch_op:
        for column in ('org_id', 'created_at', 'stage', 'model', 'status', 'job_id', 'candidate_id', 'interview_id'):
            batch_op.create_index(batch_op.f(f'ix_model_calls_{column}'), [column], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('model_calls', schema=None) as batch_op:
        for column in ('interview_id', 'candidate_id', 'job_id', 'status', 'model', 'stage', 'created_at', 'org_id'):
            batch_op.drop_index(batch_op.f(f'ix_model_calls_{column}'))
    op.drop_table('model_calls')
