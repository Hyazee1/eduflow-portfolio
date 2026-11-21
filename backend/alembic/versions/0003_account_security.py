"""Bind sessions to delivery portals and add single-use account credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_sessions", sa.Column("portal", sa.String(12), nullable=False, server_default="LEGACY")
    )
    op.create_index("ix_auth_sessions_portal", "auth_sessions", ["portal"])
    op.execute("UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, now())")
    op.create_table(
        "account_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(36)),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "id", name="uq_account_tokens_tenant_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            name="fk_account_tokens_user_id_tenant",
        ),
        sa.CheckConstraint("purpose IN ('INVITE','RESET')", name="ck_account_token_purpose"),
    )
    op.create_index("ix_account_tokens_organization_id", "account_tokens", ["organization_id"])
    op.create_index("ix_account_tokens_user_id", "account_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_table("account_tokens")
    op.drop_index("ix_auth_sessions_portal", table_name="auth_sessions")
    op.drop_column("auth_sessions", "portal")
