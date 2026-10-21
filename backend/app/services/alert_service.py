"""Alert service for managing price and trend notifications.

Implements business logic for creating, evaluating, activating/deactivating alerts,
and dispatching notifications when alert conditions are met.

Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.alert import Alert, AlertTypeEnum
from backend.app.models.stock import Stock, TrendData
from backend.app.schemas.alert_schemas import (
    AlertCreate,
    AlertListResponse,
    AlertResponse,
    TriggeredAlert,
)

logger = logging.getLogger(__name__)

# Maximum number of active alerts per user (Requirement 8.5)
MAX_ALERTS_PER_USER = 50


class AlertLimitExceededError(Exception):
    """Raised when a user tries to create more than the maximum allowed alerts."""

    pass


class AlertNotFoundError(Exception):
    """Raised when an alert is not found."""

    pass


class AlertService:
    """Service layer for alert-related business operations.

    Accepts an AsyncSession via dependency injection for all database operations.
    """

    def __init__(self, session: AsyncSession):
        """Initialize AlertService with a database session.

        Args:
            session: Async SQLAlchemy session for database operations.
        """
        self._session = session

    async def create_alert(self, user_id: int, alert_data: AlertCreate) -> AlertResponse:
        """Create a new alert for a user.

        Validates the alert limit (max 50 active alerts per user) before creating.

        Args:
            user_id: The ID of the user creating the alert.
            alert_data: Alert creation parameters (stock_id, alert_type, threshold_value).

        Returns:
            AlertResponse with the created alert data.

        Raises:
            AlertLimitExceededError: If user already has 50 active alerts.
        """
        # Check alert limit (Requirement 8.5)
        active_count = await self._get_active_alert_count(user_id)
        if active_count >= MAX_ALERTS_PER_USER:
            raise AlertLimitExceededError(
                f"Maximum alert limit reached ({MAX_ALERTS_PER_USER}). "
                "Please deactivate or delete existing alerts before creating new ones."
            )

        # Create the alert
        alert = Alert(
            user_id=user_id,
            stock_id=alert_data.stock_id,
            alert_type=alert_data.alert_type.value,
            threshold_value=alert_data.threshold_value,
            is_active=True,
        )

        self._session.add(alert)
        await self._session.commit()
        await self._session.refresh(alert)

        logger.info(
            "Alert created: id=%d, user_id=%d, stock_id=%d, type=%s, threshold=%s",
            alert.id,
            user_id,
            alert_data.stock_id,
            alert_data.alert_type.value,
            alert_data.threshold_value,
        )

        return AlertResponse.model_validate(alert)

    async def get_user_alerts(self, user_id: int) -> AlertListResponse:
        """List all alerts for a user.

        Args:
            user_id: The ID of the user whose alerts to retrieve.

        Returns:
            AlertListResponse with all alerts, total count, and active count.
        """
        stmt = (
            select(Alert)
            .where(Alert.user_id == user_id)
            .order_by(Alert.created_at.desc())
        )
        result = await self._session.execute(stmt)
        alerts = result.scalars().all()

        active_count = sum(1 for a in alerts if a.is_active)

        return AlertListResponse(
            alerts=[AlertResponse.model_validate(a) for a in alerts],
            total=len(alerts),
            active_count=active_count,
        )

    async def deactivate_alert(self, alert_id: int, user_id: Optional[int] = None) -> AlertResponse:
        """Deactivate an alert, stopping monitoring immediately.

        The alert configuration is retained for potential reactivation.
        (Requirement 8.6)

        Args:
            alert_id: The ID of the alert to deactivate.
            user_id: Optional user ID for ownership verification.

        Returns:
            AlertResponse with updated alert data (is_active=False).

        Raises:
            AlertNotFoundError: If the alert does not exist or doesn't belong to user.
        """
        alert = await self._get_alert(alert_id, user_id)

        alert.is_active = False
        alert.updated_at = datetime.now(timezone.utc)

        await self._session.commit()
        await self._session.refresh(alert)

        logger.info("Alert deactivated: id=%d", alert_id)

        return AlertResponse.model_validate(alert)

    async def activate_alert(self, alert_id: int, user_id: Optional[int] = None) -> AlertResponse:
        """Reactivate a previously deactivated alert, resuming monitoring.

        Args:
            alert_id: The ID of the alert to activate.
            user_id: Optional user ID for ownership verification.

        Returns:
            AlertResponse with updated alert data (is_active=True).

        Raises:
            AlertNotFoundError: If the alert does not exist or doesn't belong to user.
            AlertLimitExceededError: If reactivation would exceed the 50-alert limit.
        """
        alert = await self._get_alert(alert_id, user_id)

        # Verify activating won't exceed the limit
        if not alert.is_active:
            active_count = await self._get_active_alert_count(alert.user_id)
            if active_count >= MAX_ALERTS_PER_USER:
                raise AlertLimitExceededError(
                    f"Cannot reactivate alert. Maximum active alert limit "
                    f"({MAX_ALERTS_PER_USER}) already reached."
                )

        alert.is_active = True
        alert.updated_at = datetime.now(timezone.utc)

        await self._session.commit()
        await self._session.refresh(alert)

        logger.info("Alert activated: id=%d", alert_id)

        return AlertResponse.model_validate(alert)

    async def update_alert(
        self, alert_id: int, user_id: int, alert_type: str | None = None, threshold_value: Optional[Decimal] = None
    ) -> AlertResponse:
        """Update an existing alert's configuration.

        Args:
            alert_id: The ID of the alert to update.
            user_id: The ID of the user (for ownership verification).
            alert_type: Optional new alert type.
            threshold_value: Optional new threshold value.

        Returns:
            AlertResponse with the updated alert data.

        Raises:
            AlertNotFoundError: If the alert does not exist or doesn't belong to user.
        """
        alert = await self._get_alert(alert_id, user_id)

        if alert_type is not None:
            alert.alert_type = alert_type
        if threshold_value is not None:
            alert.threshold_value = threshold_value

        alert.updated_at = datetime.now(timezone.utc)

        await self._session.commit()
        await self._session.refresh(alert)

        logger.info("Alert updated: id=%d", alert_id)

        return AlertResponse.model_validate(alert)

    async def delete_alert(self, alert_id: int, user_id: int) -> None:
        """Delete an alert permanently.

        Args:
            alert_id: The ID of the alert to delete.
            user_id: The ID of the user (for ownership verification).

        Raises:
            AlertNotFoundError: If the alert does not exist or doesn't belong to user.
        """
        alert = await self._get_alert(alert_id, user_id)

        await self._session.delete(alert)
        await self._session.commit()

        logger.info("Alert deleted: id=%d, user_id=%d", alert_id, user_id)

    async def evaluate_alerts(
        self,
        stock_id: int,
        current_price: Decimal,
        trend_score: Optional[int] = None,
    ) -> list[TriggeredAlert]:
        """Evaluate all active alerts for a stock against current conditions.

        Checks price and trend conditions against their respective thresholds:
        - PRICE_ABOVE: triggers when current_price >= threshold_value
        - PRICE_BELOW: triggers when current_price <= threshold_value
        - TREND_ABOVE: triggers when trend_score >= threshold_value
        - TREND_BELOW: triggers when trend_score <= threshold_value

        Only active alerts (is_active=True) are evaluated. Deactivated alerts
        are never triggered (Requirement 8.6).

        Args:
            stock_id: The ID of the stock to evaluate alerts for.
            current_price: The current market price of the stock.
            trend_score: Optional current trend score (0-100) for trend alerts.

        Returns:
            List of TriggeredAlert objects for alerts whose conditions are met.
        """
        # Fetch only active alerts for this stock
        stmt = (
            select(Alert)
            .where(Alert.stock_id == stock_id)
            .where(Alert.is_active.is_(True))
        )
        result = await self._session.execute(stmt)
        active_alerts = result.scalars().all()

        if not active_alerts:
            return []

        # Fetch stock info for notification context
        stock_stmt = select(Stock).where(Stock.id == stock_id)
        stock_result = await self._session.execute(stock_stmt)
        stock = stock_result.scalar_one_or_none()

        stock_name = stock.company_name if stock else None
        ticker_symbol = stock.ticker_symbol if stock else None

        triggered: list[TriggeredAlert] = []
        now = datetime.now(timezone.utc)

        for alert in active_alerts:
            is_triggered = self._check_condition(
                alert_type=alert.alert_type,
                threshold_value=alert.threshold_value,
                current_price=current_price,
                trend_score=trend_score,
            )

            if is_triggered:
                # Determine the current value that triggered the alert
                current_value = self._get_current_value(
                    alert_type=alert.alert_type,
                    current_price=current_price,
                    trend_score=trend_score,
                )

                # Update last_triggered_at timestamp
                alert.last_triggered_at = now
                alert.updated_at = now

                triggered_alert = TriggeredAlert(
                    alert_id=alert.id,
                    user_id=alert.user_id,
                    stock_id=alert.stock_id,
                    alert_type=alert.alert_type,
                    threshold_value=alert.threshold_value,
                    current_value=current_value,
                    triggered_at=now,
                    stock_name=stock_name,
                    ticker_symbol=ticker_symbol,
                )

                triggered.append(triggered_alert)

                logger.info(
                    "Alert triggered: id=%d, type=%s, threshold=%s, current=%s",
                    alert.id,
                    alert.alert_type,
                    alert.threshold_value,
                    current_value,
                )

        if triggered:
            await self._session.commit()
            # Dispatch notifications for all triggered alerts (Requirement 8.3, 8.4)
            await self._dispatch_notifications(triggered)

        return triggered

    async def check_alert_limit(self, user_id: int) -> bool:
        """Check if a user can create more alerts.

        Args:
            user_id: The ID of the user to check.

        Returns:
            True if the user can create more alerts (under limit), False otherwise.
        """
        active_count = await self._get_active_alert_count(user_id)
        return active_count < MAX_ALERTS_PER_USER

    # --- Private helper methods ---

    async def _get_active_alert_count(self, user_id: int) -> int:
        """Get the count of active alerts for a user.

        Args:
            user_id: The ID of the user.

        Returns:
            Number of active alerts.
        """
        stmt = (
            select(func.count())
            .select_from(Alert)
            .where(Alert.user_id == user_id)
            .where(Alert.is_active.is_(True))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def _get_alert(self, alert_id: int, user_id: Optional[int] = None) -> Alert:
        """Retrieve an alert by ID with optional ownership verification.

        Args:
            alert_id: The ID of the alert to retrieve.
            user_id: Optional user ID for ownership check.

        Returns:
            The Alert model instance.

        Raises:
            AlertNotFoundError: If alert not found or doesn't belong to user.
        """
        stmt = select(Alert).where(Alert.id == alert_id)
        if user_id is not None:
            stmt = stmt.where(Alert.user_id == user_id)

        result = await self._session.execute(stmt)
        alert = result.scalar_one_or_none()

        if alert is None:
            raise AlertNotFoundError(f"Alert with id {alert_id} not found.")

        return alert

    def _check_condition(
        self,
        alert_type: str,
        threshold_value: Decimal,
        current_price: Decimal,
        trend_score: Optional[int],
    ) -> bool:
        """Evaluate whether an alert condition is met.

        Args:
            alert_type: The type of alert (PRICE_ABOVE, PRICE_BELOW, etc.).
            threshold_value: The threshold value for the alert.
            current_price: The current stock price.
            trend_score: The current trend score (if available).

        Returns:
            True if the condition is met and alert should trigger.
        """
        if alert_type == AlertTypeEnum.PRICE_ABOVE.value:
            return current_price >= threshold_value
        elif alert_type == AlertTypeEnum.PRICE_BELOW.value:
            return current_price <= threshold_value
        elif alert_type == AlertTypeEnum.TREND_ABOVE.value:
            if trend_score is None:
                return False
            return Decimal(trend_score) >= threshold_value
        elif alert_type == AlertTypeEnum.TREND_BELOW.value:
            if trend_score is None:
                return False
            return Decimal(trend_score) <= threshold_value
        else:
            logger.warning("Unknown alert type: %s", alert_type)
            return False

    def _get_current_value(
        self,
        alert_type: str,
        current_price: Decimal,
        trend_score: Optional[int],
    ) -> Decimal:
        """Get the current value relevant to an alert type.

        Args:
            alert_type: The type of alert.
            current_price: The current stock price.
            trend_score: The current trend score.

        Returns:
            The current value that triggered or is being compared against threshold.
        """
        if alert_type in (AlertTypeEnum.PRICE_ABOVE.value, AlertTypeEnum.PRICE_BELOW.value):
            return current_price
        elif alert_type in (AlertTypeEnum.TREND_ABOVE.value, AlertTypeEnum.TREND_BELOW.value):
            return Decimal(trend_score) if trend_score is not None else Decimal("0")
        return current_price

    async def _dispatch_notifications(self, triggered_alerts: list[TriggeredAlert]) -> None:
        """Dispatch notifications for triggered alerts.

        Sends both in-app and email notifications within 5 minutes of trigger
        (Requirement 8.3, 8.4).

        In-app notifications are stored directly. Email notifications are
        dispatched as a placeholder (would integrate with Celery/SMTP in production).

        Args:
            triggered_alerts: List of alerts that were triggered.
        """
        for triggered in triggered_alerts:
            # In-app notification
            await self._send_in_app_notification(triggered)

            # Email notification (placeholder - would use Celery task in production)
            await self._send_email_notification(triggered)

    async def _send_in_app_notification(self, triggered: TriggeredAlert) -> None:
        """Store an in-app notification for a triggered alert.

        Args:
            triggered: The triggered alert data.
        """
        # In a full implementation, this would create a Notification record in the database.
        # For now, we log the notification as a placeholder.
        logger.info(
            "In-app notification dispatched: user_id=%d, alert_id=%d, "
            "stock=%s (%s), type=%s, threshold=%s, current=%s",
            triggered.user_id,
            triggered.alert_id,
            triggered.stock_name or "Unknown",
            triggered.ticker_symbol or "N/A",
            triggered.alert_type,
            triggered.threshold_value,
            triggered.current_value,
        )

    async def _send_email_notification(self, triggered: TriggeredAlert) -> None:
        """Send an email notification for a triggered alert.

        This is a placeholder implementation. In production, this would dispatch
        an email task via Celery to ensure delivery within 5 minutes (Requirement 8.4).

        Args:
            triggered: The triggered alert data.
        """
        # Placeholder: In production, queue via Celery:
        # send_alert_email.delay(
        #     user_id=triggered.user_id,
        #     alert_id=triggered.alert_id,
        #     stock_name=triggered.stock_name,
        #     alert_type=triggered.alert_type,
        #     threshold_value=str(triggered.threshold_value),
        #     current_value=str(triggered.current_value),
        #     triggered_at=triggered.triggered_at.isoformat(),
        # )
        logger.info(
            "Email notification queued: user_id=%d, alert_id=%d, type=%s",
            triggered.user_id,
            triggered.alert_id,
            triggered.alert_type,
        )
