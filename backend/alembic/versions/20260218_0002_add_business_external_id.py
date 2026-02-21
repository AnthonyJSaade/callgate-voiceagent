"""Add external_id to businesses for tenant resolution.

Revision ID: 20260218_0002
Revises: 20260218_0001
Create Date: 2026-02-18 00:00:02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260218_0002"
down_revision: Union[str, None] = "20260218_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.create_index(
        "ix_businesses_external_id", "businesses", ["external_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_businesses_external_id", table_name="businesses")
    op.drop_column("businesses", "external_id")
