"""Pydantic schemas for Alert endpoints."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AlertType(str, Enum):
    """Supported alert trigger types."""

    PRICE_ABOVE = "PRICE_ABOVE"
    PRICE_BELOW = "PRICE_BELOW"
    TREND_ABOVE = "TREND_ABOVE"
    TREND_BELOW = "TREND_BELOW"


class AlertCreate(BaseModel):
    """Schema for creating a new alert."""

    stock_id: int = Field(..., description="ID of the stock to monitor")
    alert_type: AlertType = Field(..., description="Type of alert condition")
    threshold_value: Decimal = Field(
        ..., gt=0, description="Target value that triggers the alert"
    )


class AlertUpdate(BaseModel):
    """Schema for updating an existing alert."""

    alert_type: Optional[AlertType] = Field(None, description="Updated alert type")
    threshold_value: Optional[Decimal] = Field(
        None, gt=0, description="Updated threshold value"
    )


class AlertResponse(BaseModel):
    """Schema for alert data returned from the API."""

    id: int
    user_id: int
    stock_id: int
    alert_type: AlertType
    threshold_value: Decimal
    is_active: bool
    last_triggered_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AlertListResponse(BaseModel):
    """Schema for a paginated list of alerts."""

    alerts: list[AlertResponse]
    total: int
    active_count: int


class TriggeredAlert(BaseModel):
    """Schema representing an alert that has been triggered."""

    alert_id: int
    user_id: int
    stock_id: int
    alert_type: AlertType
    threshold_value: Decimal
    current_value: Decimal
    triggered_at: datetime
    stock_name: Optional[str] = None
    ticker_symbol: Optional[str] = None
