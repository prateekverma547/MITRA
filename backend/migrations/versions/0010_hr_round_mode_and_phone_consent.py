"""HR round mode and phone consent

The HR round can now be a phone call MITRA listens to (`mode = "listen"`), as well
as MITRA's own voice room (`"room"`, every round before this). In a phone call the
candidate never sees MITRA's consent screen, so HR confirms they agreed:
`phone_consent_at`.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-20 02:40:09.949102
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('hr_rounds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mode', sa.String(length=8), server_default='room', nullable=False))
        batch_op.add_column(sa.Column('phone_consent_at', sa.DateTime(timezone=True), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('hr_rounds', schema=None) as batch_op:
        batch_op.drop_column('phone_consent_at')
        batch_op.drop_column('mode')

