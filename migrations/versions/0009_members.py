"""v34 회원: users · user_sessions · user_tokens + 사람별 소유(owner) 컬럼

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(60)),
        sa.Column("pw_hash", sa.String(200), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("plan", sa.String(12), nullable=False),
        sa.Column("plan_until", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("totp_secret", sa.String(300)),
        sa.Column("totp_last", sa.BigInteger),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("consent", JSONType),
        sa.Column("failed", sa.Integer, nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("pw_changed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("ip", sa.String(64)),
        sa.Column("agent", sa.String(200)),
    )
    op.create_table(
        "user_tokens",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("user_id", sa.Integer, index=True),
        sa.Column("data", JSONType),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    for table in ("alerts", "alert_rules", "user_journal"):
        op.add_column(table, sa.Column("owner", sa.Integer))
        op.create_index(f"ix_{table}_owner", table, ["owner"])


def downgrade() -> None:
    for table in ("alerts", "alert_rules", "user_journal"):
        op.drop_index(f"ix_{table}_owner", table_name=table)
        op.drop_column(table, "owner")
    op.drop_table("user_tokens")
    op.drop_table("user_sessions")
    op.drop_table("users")
