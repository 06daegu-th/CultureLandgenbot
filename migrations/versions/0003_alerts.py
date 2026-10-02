"""site alerts (toasts, alert center)

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 앱의 init_db(create_all) 가 먼저 만들었을 수도 있다
    if "alerts" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("link", sa.String(length=256), nullable=True),
        sa.Column("dedupe", sa.String(length=160), nullable=True, unique=True),
        sa.Column("data", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
    )
    op.create_index("ix_alerts_ts", "alerts", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_alerts_ts", table_name="alerts")
    op.drop_table("alerts")
