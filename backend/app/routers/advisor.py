"""Advisor chat router (Step 5 / G5 — grounded portfolio chat).

``POST /api/v1/advisor/chat`` answers a user's question about THEIR portfolio,
grounded in real stored data (holdings + latest stored quant/deep recs). It is:

  * auth-guarded and user-scoped — ``Depends(get_current_user)`` like the other
    user-scoped routers (portfolio/alerts); the answer only ever sees the
    current user's own data;
  * the QUICK/chat path, which MAY run the model inside a web request (the gateway
    serves this as interactive /quick) — but ONLY through ``gateway.client`` via
    the service, never Ollama directly or a paid API (STEERING rule 1);
  * NOT the deep lane — this module never imports the deep adapter; the deep
    lane stays a queued Celery job (STEERING rule 6).

A gateway/Ollama outage surfaces as :class:`AdvisorUnavailable` from the service
and is mapped to HTTP 503 ``advisor_model_unavailable`` here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_current_user, get_db
from backend.app.schemas.advisor_schemas import AdvisorAnswer, AdvisorQuestion
from backend.app.services import advisor_service
from backend.app.services.advisor_service import AdvisorUnavailable

router = APIRouter(prefix="/api/v1/advisor", tags=["advisor"])


@router.post("/chat", response_model=AdvisorAnswer)
async def advisor_chat(
    payload: AdvisorQuestion,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> AdvisorAnswer:
    """Answer the current user's portfolio question, grounded in stored data.

    The question is already length-bounded by the schema; we re-check it is
    non-empty after trimming so a whitespace-only body is a clean 422. The model
    call runs through the gateway inside the service; a gateway outage becomes a
    503 the UI can show as "advisor unavailable".
    """
    question = payload.question.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="question must not be empty",
        )

    history = (
        [turn.model_dump() for turn in payload.history] if payload.history else None
    )

    try:
        result = await advisor_service.answer(
            db, current_user["id"], question, history
        )
    except AdvisorUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="advisor_model_unavailable",
        ) from exc

    return AdvisorAnswer.model_validate(result)
