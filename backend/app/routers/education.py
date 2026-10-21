"""Education router for learning modules, glossary, and progress tracking."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_current_user, get_db
from backend.app.schemas.report_schemas import (
    DifficultyLevel,
    EducationModuleListResponse,
    EducationModuleResponse,
    EducationProgressResponse,
    GlossaryTerm,
    UserEducationSummary,
)
from backend.app.services.education_service import (
    get_contextual_suggestions,
    get_glossary,
    get_module_content,
    get_modules_by_level,
    get_user_progress,
    mark_completed,
)
from backend.app.models.education import EducationProgress
from sqlalchemy import select

router = APIRouter(prefix="/api/v1/education", tags=["Education"])


@router.get("/modules", response_model=EducationModuleListResponse)
async def list_modules(
    difficulty: DifficultyLevel = Query(
        DifficultyLevel.BEGINNER,
        description="Filter modules by difficulty level",
    ),
    db: AsyncSession = Depends(get_db),
):
    """List educational modules filtered by difficulty level."""
    modules = await get_modules_by_level(db, difficulty.value)
    return EducationModuleListResponse(modules=modules, total=len(modules))


@router.get("/glossary", response_model=list[GlossaryTerm])
async def get_glossary_terms():
    """Get stock market glossary terms."""
    terms = get_glossary()
    return [GlossaryTerm(term=t["term"], definition=t["definition"]) for t in terms]


@router.get("/suggestions", response_model=list[EducationModuleResponse])
async def get_suggestions(
    sector: str = Query(..., description="Stock sector to get suggestions for"),
    db: AsyncSession = Depends(get_db),
):
    """Get contextual educational suggestions based on a stock's sector."""
    modules = await get_contextual_suggestions(db, sector)
    return modules


@router.get("/progress", response_model=UserEducationSummary)
async def get_learning_progress(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get the current user's learning progress summary. Requires authentication."""
    user_id = current_user["id"]
    progress_data = await get_user_progress(db, user_id)

    # Fetch completed progress records for the user
    stmt = select(EducationProgress).where(
        EducationProgress.user_id == user_id,
        EducationProgress.is_completed.is_(True),
    )
    result = await db.execute(stmt)
    completed_records = list(result.scalars().all())

    return UserEducationSummary(
        total_modules=progress_data["total_modules"],
        completed_modules=progress_data["completed_count"],
        progress_pct=progress_data["completion_percentage"],
        completed=completed_records,
    )


@router.get("/modules/{module_id}", response_model=EducationModuleResponse)
async def get_module(
    module_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the full content of a specific education module."""
    module = await get_module_content(db, module_id)
    if module is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Education module with id {module_id} not found",
        )
    return module


@router.post(
    "/modules/{module_id}/complete",
    response_model=EducationProgressResponse,
    status_code=status.HTTP_200_OK,
)
async def complete_module(
    module_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Mark an education module as completed. Requires authentication."""
    user_id = current_user["id"]

    # Verify module exists
    module = await get_module_content(db, module_id)
    if module is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Education module with id {module_id} not found",
        )

    progress = await mark_completed(db, user_id, module_id)
    return progress
