"""Idempotent account administration without persisting raw credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("account_tokens", sa.Column("request_key", sa.String(128), nullable=True))
    op.add_column(
        "account_tokens", sa.Column("request_fingerprint", sa.String(64), nullable=False, server_default="")
    )
    op.create_unique_constraint(
        "uq_account_token_request", "account_tokens", ["organization_id", "created_by", "request_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_account_token_request", "account_tokens", type_="unique")
    op.drop_column("account_tokens", "request_fingerprint")
    op.drop_column("account_tokens", "request_key")
