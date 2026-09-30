"""data provenance: collected_at on news/disclosures, summarized_at on disclosures

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _add(table: str, col: str) -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    if col not in have:
        op.add_column(table, sa.Column(col, sa.DateTime(timezone=True), nullable=True))


def upgrade() -> None:
    _add("news_articles", "collected_at")
    _add("disclosures", "collected_at")
    _add("disclosures", "summarized_at")


def downgrade() -> None:
    op.drop_column("disclosures", "summarized_at")
    op.drop_column("disclosures", "collected_at")
    op.drop_column("news_articles", "collected_at")
