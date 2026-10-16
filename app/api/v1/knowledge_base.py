from __future__ import annotations

import math
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.database import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.schemas.knowledge_base import (
    KBEntryCreate,
    KBEntryOut,
    KBEntryUpdate,
    KBListResponse,
)

router = APIRouter(prefix="/knowledge-base", tags=["Knowledge Base"])


def validate_uuid(id_str: str) -> None:
    try:
        uuid.UUID(id_str)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base entry not found",
        )


@router.get(
    "",
    response_model=KBListResponse,
    summary="List knowledge base entries (Admin only)",
)
async def list_kb_entries(
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    conditions = []
    if category:
        conditions.append(KnowledgeBase.category == category)
    if is_active is not None:
        conditions.append(KnowledgeBase.is_active.is_(is_active))
    if search:
        search_pattern = f"%{search}%"
        conditions.append(
            or_(
                KnowledgeBase.question.ilike(search_pattern),
                KnowledgeBase.answer.ilike(search_pattern),
            )
        )

    # Count
    count_stmt = select(func.count(KnowledgeBase.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    c_res = await db.execute(count_stmt)
    total = c_res.scalar_one()

    # Query
    offset = (page - 1) * limit
    stmt = (
        select(KnowledgeBase)
        .order_by(KnowledgeBase.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if conditions:
        stmt = stmt.where(*conditions)

    res = await db.execute(stmt)
    items = list(res.scalars().all())
    pages = math.ceil(total / limit) if limit > 0 else 1

    return KBListResponse(
        items=[KBEntryOut.model_validate(it) for it in items],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.post(
    "",
    response_model=KBEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a knowledge base FAQ entry (Admin only)",
)
async def create_kb_entry(
    payload: KBEntryCreate,
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    entry = KnowledgeBase(
        question=payload.question,
        answer=payload.answer,
        category=payload.category,
        is_active=payload.is_active,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return KBEntryOut.model_validate(entry)


@router.get(
    "/{id}",
    response_model=KBEntryOut,
    summary="Get single knowledge base entry by ID (Admin only)",
)
async def get_kb_entry(
    id: str,
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    validate_uuid(id)

    stmt = select(KnowledgeBase).where(KnowledgeBase.id == id)
    res = await db.execute(stmt)
    entry = res.scalar_one_or_none()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base entry not found",
        )
    return KBEntryOut.model_validate(entry)


@router.put(
    "/{id}",
    response_model=KBEntryOut,
    summary="Update knowledge base entry (Admin only)",
)
async def update_kb_entry(
    id: str,
    payload: KBEntryUpdate,
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    validate_uuid(id)

    stmt = select(KnowledgeBase).where(KnowledgeBase.id == id)
    res = await db.execute(stmt)
    entry = res.scalar_one_or_none()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base entry not found",
        )

    if payload.question is not None:
        entry.question = payload.question
    if payload.answer is not None:
        entry.answer = payload.answer
    if payload.category is not None:
        entry.category = payload.category
    if payload.is_active is not None:
        entry.is_active = payload.is_active

    await db.commit()
    await db.refresh(entry)
    return KBEntryOut.model_validate(entry)


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete knowledge base entry (Admin only)",
)
async def delete_kb_entry(
    id: str,
    current_user: Annotated[User, Depends(require_role("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    validate_uuid(id)

    stmt = select(KnowledgeBase).where(KnowledgeBase.id == id)
    res = await db.execute(stmt)
    entry = res.scalar_one_or_none()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base entry not found",
        )

    await db.delete(entry)
    await db.commit()
