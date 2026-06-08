"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-08
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("topic", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="scripted"),
        sa.Column("spec_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project_id"),
    )


def downgrade() -> None:
    op.drop_table("projects")
