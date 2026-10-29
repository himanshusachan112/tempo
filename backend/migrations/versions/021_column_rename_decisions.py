"""column_rename_decisions: reviewer-accepted column renamings (MCR-D1..D5), separate from discrepancy_findings

Revision ID: 021
Revises: 020
Create Date: 2026-10-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "column_rename_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("suggestion_key", sa.String(300), nullable=False),
        sa.Column("rule_id", sa.String(20), nullable=False),
        sa.Column("target_field", sa.String(255), nullable=False),
        sa.Column("source_column", sa.String(255), nullable=False),
        sa.Column("original_meaning", sa.String(100), nullable=False),
        sa.Column("new_meaning", sa.String(100), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_column_rename_decisions")),
        sa.ForeignKeyConstraint(
            ["run_id"], ["processing_runs.id"], name=op.f("fk_column_rename_decisions_run_id_processing_runs")
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], name=op.f("fk_column_rename_decisions_decided_by_users")),
        sa.UniqueConstraint("run_id", "suggestion_key", name="uq_column_rename_decisions_run_key"),
    )
    op.create_index(op.f("ix_column_rename_decisions_run_id"), "column_rename_decisions", ["run_id"])


def downgrade() -> None:
    op.drop_table("column_rename_decisions")
