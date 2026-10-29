"""column_rename_decisions: a reviewer's acceptance of a suggested column renaming (MCR-D1..D5).

Kept apart from ``discrepancy_findings`` on purpose: the original findings are never touched.
A decision only feeds the separate *revised* discrepancy view; deleting it restores the original reading.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bordereaux.models.base import Base, CreatedAtMixin, UUIDMixin, uuid_fk_type


class ColumnRenameDecision(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "column_rename_decisions"
    __table_args__ = (
        UniqueConstraint("run_id", "suggestion_key", name="uq_column_rename_decisions_run_key"),
        Index("ix_column_rename_decisions_run_id", "run_id"),
    )

    run_id: Mapped[UUID] = mapped_column(uuid_fk_type(), ForeignKey("processing_runs.id"), nullable=False)
    suggestion_key: Mapped[str] = mapped_column(String(300), nullable=False)  # "MCR-D1:gross_premium"
    rule_id: Mapped[str] = mapped_column(String(20), nullable=False)
    target_field: Mapped[str] = mapped_column(String(255), nullable=False)
    source_column: Mapped[str] = mapped_column(String(255), nullable=False)
    original_meaning: Mapped[str] = mapped_column(String(100), nullable=False)
    new_meaning: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[UUID | None] = mapped_column(uuid_fk_type(), ForeignKey("users.id"))
