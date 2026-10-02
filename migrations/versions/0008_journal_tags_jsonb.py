"""user_journal.tags: json -> jsonb on PostgreSQL (모델과 같게 — 0005 가 plain JSON 으로 만들었음)

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _pg_type() -> str | None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return None
    col = next((c for c in sa.inspect(bind).get_columns("user_journal") if c["name"] == "tags"), None)
    return type(col["type"]).__name__.upper() if col else None


def upgrade() -> None:
    if _pg_type() == "JSON":  # SQLite·이미 JSONB 면 아무것도 안 함
        op.alter_column("user_journal", "tags", type_=postgresql.JSONB(astext_type=sa.Text()),
                        existing_type=postgresql.JSON(astext_type=sa.Text()), postgresql_using="tags::jsonb")


def downgrade() -> None:
    if _pg_type() == "JSONB":
        op.alter_column("user_journal", "tags", type_=postgresql.JSON(astext_type=sa.Text()),
                        existing_type=postgresql.JSONB(astext_type=sa.Text()), postgresql_using="tags::json")
