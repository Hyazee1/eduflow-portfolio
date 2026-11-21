"""Give transactional workflow events a stable insertion order."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "change_request_events", sa.Column("sequence", sa.BigInteger(), sa.Identity(), nullable=False)
    )
    op.create_unique_constraint("uq_change_event_sequence", "change_request_events", ["sequence"])


def downgrade() -> None:
    op.drop_constraint("uq_change_event_sequence", "change_request_events", type_="unique")
    op.drop_column("change_request_events", "sequence")
