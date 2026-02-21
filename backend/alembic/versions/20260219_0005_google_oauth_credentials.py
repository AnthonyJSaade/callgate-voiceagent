"""Add google OAuth credential storage.

Revision ID: 20260219_0005
Revises: 20260219_0004
Create Date: 2026-02-19 00:00:05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260219_0005"
down_revision: Union[str, None] = "20260219_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "google_oauth_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("business_id", name="uq_google_oauth_credentials_business_id"),
    )
    op.create_index(
        "ix_google_oauth_credentials_business_id",
        "google_oauth_credentials",
        ["business_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_google_oauth_credentials_business_id", table_name="google_oauth_credentials")
    op.drop_table("google_oauth_credentials")
