"""user journal (my decisions vs AI)

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "user_journal" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "user_journal",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("symbol", sa.String(length=32), nullable=False),
            sa.Column("action", sa.String(length=8), nullable=False),
            sa.Column("conviction", sa.Integer(), nullable=False),
            sa.Column("horizon", sa.Integer(), nullable=False),
            sa.Column("ref_price", sa.Float(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("tags", sa.JSON(), nullable=True),
            sa.Column("row_hash", sa.String(length=64), nullable=True),
            sa.Column("realized_return", sa.Float(), nullable=True),
            sa.Column("correct", sa.Boolean(), nullable=True),
        )
        op.create_index("ix_user_journal_symbol", "user_journal", ["symbol", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_user_journal_symbol", "user_journal")
    op.drop_table("user_journal")
