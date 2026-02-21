"""Add admin calendar fields on businesses and bookings.

Revision ID: 20260219_0004
Revises: 20260218_0003
Create Date: 2026-02-19 00:00:04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260219_0004"
down_revision: Union[str, None] = "20260218_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("calendar_provider", sa.Text(), nullable=True))
    op.add_column("businesses", sa.Column("calendar_account_id", sa.Text(), nullable=True))
    op.add_column("businesses", sa.Column("calendar_id", sa.Text(), nullable=True))
    op.add_column(
        "businesses",
        sa.Column(
            "calendar_oauth_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'not_connected'"),
        ),
    )
    op.add_column(
        "businesses",
        sa.Column(
            "calendar_settings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.add_column("bookings", sa.Column("external_event_id", sa.Text(), nullable=True))
    op.add_column("bookings", sa.Column("external_event_provider", sa.Text(), nullable=True))
    op.create_index(
        "ix_bookings_external_event_id", "bookings", ["external_event_id"], unique=False
    )

    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_businesses_external_id ON businesses (external_id)"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_bookings_external_event_id", table_name="bookings")
    op.drop_column("bookings", "external_event_provider")
    op.drop_column("bookings", "external_event_id")

    op.drop_column("businesses", "calendar_settings_json")
    op.drop_column("businesses", "calendar_oauth_status")
    op.drop_column("businesses", "calendar_id")
    op.drop_column("businesses", "calendar_account_id")
    op.drop_column("businesses", "calendar_provider")
