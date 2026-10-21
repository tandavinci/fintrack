"""Alert management router.

Provides endpoints for creating, listing, updating, deleting, activating,
and deactivating price/trend alerts. All endpoints require authentication.

Validates: Requirements 8.1, 8.2, 8.5, 8.6
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_current_user, get_db
from backend.app.schemas.alert_schemas import (
    AlertCreate,
    AlertListResponse,
    AlertResponse,
    AlertUpdate,
)
from backend.app.services.alert_service import (
    AlertLimitExceededError,
    AlertNotFoundError,
    AlertService,
)

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


def get_alert_service(db: AsyncSession = Depends(get_db)) -> AlertService:
    """Dependency to provide an AlertService instance with the current DB session."""
    return AlertService(session=db)


# --- List alerts ---


@router.get("/", response_model=AlertListResponse)
async def list_alerts(
    current_user: dict = Depends(get_current_user),
    service: AlertService = Depends(get_alert_service),
) -> AlertListResponse:
    """List all alerts for the authenticated user.

    Returns alerts with total count and active count.
    """
    return await service.get_user_alerts(current_user["id"])


# --- Create alert ---


@router.post("/", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
async def create_alert(
    alert_data: AlertCreate,
    current_user: dict = Depends(get_current_user),
    service: AlertService = Depends(get_alert_service),
) -> AlertResponse:
    """Create a new price or trend alert.

    Validates the user's alert limit (max 50 active alerts) before creating.
    """
    try:
        return await service.create_alert(current_user["id"], alert_data)
    except AlertLimitExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


# --- Update alert ---


@router.put("/{alert_id}", response_model=AlertResponse)
async def update_alert(
    alert_id: int,
    update_data: AlertUpdate,
    current_user: dict = Depends(get_current_user),
    service: AlertService = Depends(get_alert_service),
) -> AlertResponse:
    """Update an existing alert's configuration (type and/or threshold)."""
    try:
        return await service.update_alert(
            alert_id=alert_id,
            user_id=current_user["id"],
            alert_type=update_data.alert_type.value if update_data.alert_type else None,
            threshold_value=update_data.threshold_value,
        )
    except AlertNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


# --- Delete alert ---


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(
    alert_id: int,
    current_user: dict = Depends(get_current_user),
    service: AlertService = Depends(get_alert_service),
) -> None:
    """Delete an alert permanently."""
    try:
        await service.delete_alert(alert_id=alert_id, user_id=current_user["id"])
    except AlertNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


# --- Deactivate alert ---


@router.post("/{alert_id}/deactivate", response_model=AlertResponse)
async def deactivate_alert(
    alert_id: int,
    current_user: dict = Depends(get_current_user),
    service: AlertService = Depends(get_alert_service),
) -> AlertResponse:
    """Deactivate an alert, stopping monitoring immediately.

    The alert configuration is retained and can be reactivated later.
    """
    try:
        return await service.deactivate_alert(alert_id=alert_id, user_id=current_user["id"])
    except AlertNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


# --- Activate alert ---


@router.post("/{alert_id}/activate", response_model=AlertResponse)
async def activate_alert(
    alert_id: int,
    current_user: dict = Depends(get_current_user),
    service: AlertService = Depends(get_alert_service),
) -> AlertResponse:
    """Reactivate a previously deactivated alert, resuming monitoring.

    Validates that reactivation won't exceed the 50-alert limit.
    """
    try:
        return await service.activate_alert(alert_id=alert_id, user_id=current_user["id"])
    except AlertNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except AlertLimitExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
