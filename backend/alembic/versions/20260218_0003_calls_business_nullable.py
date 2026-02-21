"""Make calls.business_id nullable for best-effort webhook ingest.

Revision ID: 20260218_0003
Revises: 20260218_0002
Create Date: 2026-02-18 00:00:03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260218_0003"
down_revision: Union[str, None] = "20260218_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("calls", "business_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("calls", "business_id", existing_type=sa.Integer(), nullable=False)
