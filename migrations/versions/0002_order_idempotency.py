"""order idempotency, recovery, evidence and slippage columns

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

COLUMNS = [
    sa.Column("client_order_id", sa.String(length=128), nullable=True),
    sa.Column("broker_orgno", sa.String(length=16), nullable=True),
    sa.Column("consensus_id", sa.BigInteger(), nullable=True),
    sa.Column("ref_price", sa.Float(), nullable=True),
    sa.Column("filled_qty", sa.Float(), nullable=True),
    sa.Column("avg_price", sa.Float(), nullable=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    # 앱의 init_db(ensure_columns) 가 먼저 추가했을 수도 있으므로 있는 컬럼은 건너뛴다
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("orders")}
    for col in COLUMNS:
        if col.name not in have:
            op.add_column("orders", col)
    idx = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("orders")}
    if "uq_orders_client_order_id" not in idx:
        op.create_index("uq_orders_client_order_id", "orders", ["client_order_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_orders_client_order_id", table_name="orders")
    for col in reversed(COLUMNS):
        op.drop_column("orders", col.name)
