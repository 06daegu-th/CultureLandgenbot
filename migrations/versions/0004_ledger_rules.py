"""prediction ledger (created_at, row_hash, anchors), alert rules, disclosure summary

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _cols(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # 앱의 init_db 가 먼저 만들었을 수도 있으므로 있는 것은 건너뛴다
    have = _cols("consensus_signals")
    if "created_at" not in have:
        op.add_column("consensus_signals", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    if "row_hash" not in have:
        op.add_column("consensus_signals", sa.Column("row_hash", sa.String(length=64), nullable=True))
    if "summary" not in _cols("disclosures"):
        op.add_column("disclosures", sa.Column("summary", sa.Text(), nullable=True))
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "ledger_anchors" not in tables:
        op.create_table(
            "ledger_anchors",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("upto_id", sa.BigInteger(), nullable=False),
            sa.Column("n", sa.Integer(), nullable=False),
            sa.Column("digest", sa.String(length=64), nullable=False),
            sa.Column("prev_digest", sa.String(length=64), nullable=True),
        )
    if "alert_rules" not in tables:
        op.create_table(
            "alert_rules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("symbol", sa.String(length=32), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("value", sa.Float(), nullable=False),
            sa.Column("note", sa.String(length=200), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("repeat", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("alert_rules")
    op.drop_table("ledger_anchors")
    op.drop_column("disclosures", "summary")
    op.drop_column("consensus_signals", "row_hash")
    op.drop_column("consensus_signals", "created_at")
