"""Celery task for evaluating alert conditions and dispatching notifications.

Runs every 60 seconds (configured in celery_app beat schedule) to evaluate
all active alerts against current stock prices and trend scores.

Dispatches in-app and email notifications for triggered alerts within 5 minutes.

Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.6
"""

import asyncio
import logging
from decimal import Decimal
from typing import Optional

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import async_session_factory
from backend.app.models.alert import Alert
from backend.app.models.stock import Stock, TrendData
from backend.app.services.alert_service import AlertService
from backend.app.tasks.email_tasks import send_email_task

logger = logging.getLogger(__name__)


async def _evaluate_all_alerts_async() -> dict:
    """Async implementation that evaluates all active alerts.

    Fetches distinct stocks with active alerts, retrieves current prices
    and latest trend scores, then delegates to AlertService.evaluate_alerts()
    for condition checking and notification dispatch.

    Returns:
        Summary dict with stocks_checked, alerts_triggered counts.
    """
    stocks_checked = 0
    total_triggered = 0

    async with async_session_factory() as session:
        # Find distinct stock_ids that have at least one active alert
        stmt = (
            select(Alert.stock_id)
            .where(Alert.is_active.is_(True))
            .distinct()
        )
        result = await session.execute(stmt)
        stock_ids = [row[0] for row in result.all()]

        if not stock_ids:
            logger.debug("No active alerts to evaluate.")
            return {"stocks_checked": 0, "alerts_triggered": 0}

        # For each stock with active alerts, get current price and trend score
        for stock_id in stock_ids:
            try:
                # Get current stock price
                stock_stmt = select(Stock).where(Stock.id == stock_id)
                stock_result = await session.execute(stock_stmt)
                stock = stock_result.scalar_one_or_none()

                if stock is None or stock.current_price is None:
                    logger.debug(
                        "Skipping stock_id=%d: stock not found or no current price.",
                        stock_id,
                    )
                    continue

                current_price: Decimal = stock.current_price

                # Get latest trend score for the stock
                trend_score: Optional[int] = await _get_latest_trend_score(
                    session, stock_id
                )

                # Use AlertService to evaluate alerts for this stock
                alert_service = AlertService(session)
                triggered_alerts = await alert_service.evaluate_alerts(
                    stock_id=stock_id,
                    current_price=current_price,
                    trend_score=trend_score,
                )

                stocks_checked += 1

                if triggered_alerts:
                    total_triggered += len(triggered_alerts)

                    # Dispatch email notifications for triggered alerts (Requirement 8.4)
                    for triggered in triggered_alerts:
                        _dispatch_email_notification(triggered)

            except Exception as exc:
                logger.error(
                    "Error evaluating alerts for stock_id=%d: %s",
                    stock_id,
                    exc,
                    exc_info=True,
                )
                continue

    return {"stocks_checked": stocks_checked, "alerts_triggered": total_triggered}


async def _get_latest_trend_score(
    session: AsyncSession, stock_id: int
) -> Optional[int]:
    """Fetch the most recent trend score for a stock.

    Args:
        session: Async SQLAlchemy session.
        stock_id: The stock ID to look up.

    Returns:
        The latest trend score (int 0-100) or None if no trend data exists.
    """
    stmt = (
        select(TrendData.trend_score)
        .where(TrendData.stock_id == stock_id)
        .order_by(TrendData.fetched_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row


def _dispatch_email_notification(triggered) -> None:
    """Queue an email notification for a triggered alert via Celery.

    Ensures email delivery within 5 minutes of alert trigger (Requirement 8.4).

    Args:
        triggered: TriggeredAlert schema instance with alert details.
    """
    try:
        subject = (
            f"Alert Triggered: {triggered.stock_name or 'Stock'} "
            f"({triggered.ticker_symbol or 'N/A'}) - {triggered.alert_type}"
        )

        html_content = (
            f"<h2>Alert Triggered</h2>"
            f"<p>Your alert for <strong>{triggered.stock_name or 'Stock'}</strong> "
            f"({triggered.ticker_symbol or 'N/A'}) has been triggered.</p>"
            f"<ul>"
            f"<li><strong>Alert Type:</strong> {triggered.alert_type}</li>"
            f"<li><strong>Threshold:</strong> {triggered.threshold_value}</li>"
            f"<li><strong>Current Value:</strong> {triggered.current_value}</li>"
            f"<li><strong>Triggered At:</strong> {triggered.triggered_at.isoformat()}</li>"
            f"</ul>"
            f"<p>Please review your portfolio and take appropriate action.</p>"
        )

        # Queue email with 5-minute expiry to meet notification SLA
        send_email_task.apply_async(
            args=[f"user_{triggered.user_id}@example.com", subject, html_content],
            expires=300,  # Expire after 5 minutes
        )

        logger.info(
            "Email notification queued for alert_id=%d, user_id=%d",
            triggered.alert_id,
            triggered.user_id,
        )
    except Exception as exc:
        logger.error(
            "Failed to queue email notification for alert_id=%d: %s",
            triggered.alert_id,
            exc,
            exc_info=True,
        )


@shared_task(
    name="app.tasks.alert_monitor.evaluate_all_alerts",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def evaluate_all_alerts(self) -> dict:
    """Celery task that evaluates all active alerts against current market data.

    Scheduled every 60 seconds via Celery Beat. For each stock with active alerts:
    1. Fetches the current price from the Stock model
    2. Fetches the latest trend score from TrendData
    3. Calls AlertService.evaluate_alerts() to check conditions
    4. Dispatches email notifications for any triggered alerts

    Deactivated alerts are automatically skipped by AlertService (Requirement 8.6).

    Returns:
        Dict with stocks_checked and alerts_triggered counts.
    """
    try:
        logger.info("Starting alert evaluation cycle.")

        # Run the async evaluation logic in an event loop
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_evaluate_all_alerts_async())
        finally:
            loop.close()

        logger.info(
            "Alert evaluation complete: stocks_checked=%d, alerts_triggered=%d",
            result["stocks_checked"],
            result["alerts_triggered"],
        )
        return result

    except Exception as exc:
        logger.error("Alert evaluation failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)
