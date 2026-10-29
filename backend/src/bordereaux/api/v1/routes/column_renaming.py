"""Suggested column renamings (MCR-D1..D5) and the revised discrepancy view.

A separate surface next to ``financial_checks``: it reads the same run but never alters the
original discrepancy findings.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from bordereaux.auth.deps import require_permission
from bordereaux.auth.permissions import Permission
from bordereaux.auth.service import AuthenticatedUser
from bordereaux.column_renaming.service import accept_renames, get_workspace, revert_rename
from bordereaux.core.database import get_db
from bordereaux.core.utils import envelope

router = APIRouter()


class AcceptRenamesRequest(BaseModel):
    keys: list[str] = Field(..., min_length=1)
    note: str | None = Field(None, max_length=1000)


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get("/processing-runs/{run_id}/column-renamings", tags=["column-renaming"])
async def get_column_renamings(
    run_id: UUID,
    request: Request,
    _user: AuthenticatedUser = Depends(require_permission(Permission.DISCREPANCY_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Suggested renamings, which are accepted, and the revised discrepancies they produce."""
    return envelope(await get_workspace(db, request.app.state.storage, run_id), request_id=_rid(request))


@router.post("/processing-runs/{run_id}/column-renamings/accept", tags=["column-renaming"])
async def post_accept_column_renamings(
    run_id: UUID,
    payload: AcceptRenamesRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_permission(Permission.DISCREPANCY_OVERRIDE)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await accept_renames(db, request.app.state.storage, run_id, user.id, payload.keys, payload.note)
    return envelope(result, request_id=_rid(request))


@router.delete("/processing-runs/{run_id}/column-renamings", tags=["column-renaming"])
async def delete_column_renaming(
    run_id: UUID,
    key: str,
    request: Request,
    user: AuthenticatedUser = Depends(require_permission(Permission.DISCREPANCY_OVERRIDE)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Undo one accepted renaming (``?key=MCR-D1:gross_premium``); the original discrepancies are untouched."""
    return envelope(await revert_rename(db, request.app.state.storage, run_id, user.id, key), request_id=_rid(request))
