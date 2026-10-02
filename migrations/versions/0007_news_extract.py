"""news structure: extract (event/direction/confidence/summary JSON) and cluster id on news_articles

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("news_articles")}
    if "extract" not in have:
        op.add_column("news_articles", sa.Column("extract", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                                                 nullable=True))
    if "cluster" not in have:
        op.add_column("news_articles", sa.Column("cluster", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("news_articles", "cluster")
    op.drop_column("news_articles", "extract")
