"""Celery application configuration with Redis broker and Beat scheduler.

Configures background task scheduling for:
- Price updates: every 60s during market hours (9:15 AM - 3:30 PM IST, Mon-Fri)
- Trend refresh: every 24 hours
- Alert evaluation: every 60 seconds
- Insight recalculation: daily post-market (after 3:30 PM IST)
- Weekly cleanup: once per week
"""

from celery import Celery
from celery.schedules import crontab

from backend.app.config import settings

# Create Celery instance with Redis as broker and result backend
celery_app = Celery(
    "bse_tracker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

# Celery configuration
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Timezone (IST for market hour scheduling)
    timezone="Asia/Kolkata",
    enable_utc=True,
    # Task execution settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Result settings
    result_expires=3600,  # Results expire after 1 hour
    # Task discovery
    imports=[
        "app.tasks.price_updater",
        "app.tasks.trend_fetcher",
        "app.tasks.alert_monitor",
        "app.tasks.insight_calculator",
        "app.tasks.email_tasks",
        "app.tasks.quant_recommender",
        "app.tasks.deep_recommender",
        "app.tasks.signal_alerter",
    ],
)

# Route the nightly quant fast-lane to the dedicated `quant` queue (Step 3 / G3),
# and the overnight deep lane to the dedicated `deep` queue (Step 4 / G4).
# The `deep` worker MUST run at concurrency 1 (the deep lane is slow and must not
# be parallelized), operationally:
#   celery -A backend.app.celery_app worker -Q deep --concurrency=1
celery_app.conf.task_routes = {
    "app.tasks.quant_recommender.*": {"queue": "quant"},
    "app.tasks.deep_recommender.*": {"queue": "deep"},
}

# Celery Beat schedule
celery_app.conf.beat_schedule = {
    # Price updates: every 60 seconds during market hours
    # Market hours: 9:15 AM - 3:30 PM IST, Monday-Friday
    "update-stock-prices": {
        "task": "app.tasks.price_updater.update_stock_prices",
        "schedule": 60.0,  # Every 60 seconds
        "options": {
            "expires": 55,  # Expire if not picked up within 55 seconds
        },
    },
    # Trend refresh: once every 24 hours (at 6:00 AM IST before market opens)
    "refresh-trend-scores": {
        "task": "app.tasks.trend_fetcher.refresh_trend_scores",
        "schedule": crontab(hour=6, minute=0),
        "options": {
            "expires": 3600,
        },
    },
    # Alert evaluation: every 60 seconds
    "evaluate-all-alerts": {
        "task": "app.tasks.alert_monitor.evaluate_all_alerts",
        "schedule": 60.0,  # Every 60 seconds
        "options": {
            "expires": 55,
        },
    },
    # Insight recalculation: daily post-market (4:00 PM IST, after 3:30 PM close)
    "recalculate-insights": {
        "task": "app.tasks.insight_calculator.recalculate_insights",
        "schedule": crontab(hour=16, minute=0, day_of_week="mon-fri"),
        "options": {
            "expires": 3600,
        },
    },
    # Nightly quant signals: post-market (4:30 PM IST, after 3:30 PM close), Mon-Fri.
    # Routed to the dedicated `quant` queue (Step 3 / G3).
    "generate-nightly-signals": {
        "task": "app.tasks.quant_recommender.generate_nightly_signals",
        "schedule": crontab(hour=16, minute=30, day_of_week="mon-fri"),
        "options": {
            "queue": "quant",
        },
    },
    # Overnight deep signals: 1:00 AM IST (well after the 3:30 PM close and well
    # before the 09:00 IST nightly-pipeline deadline / 09:15 BSE open), Tue-Sat so
    # a run covers the prior trading day. Routed to the dedicated `deep` queue;
    # the deep worker runs at concurrency 1 (Step 4 / G4).
    "generate-deep-signals": {
        "task": "app.tasks.deep_recommender.generate_deep_signals",
        "schedule": crontab(hour=1, minute=0, day_of_week="tue-sat"),
        "options": {
            "queue": "deep",
        },
    },
    # System-generated signal alerts: 7:00 AM IST — AFTER the overnight deep run
    # (1:00 AM) and the nightly quant lane, and BEFORE the 09:00 IST pipeline
    # deadline / 09:15 BSE open. It reads the stored pipeline output (quant recs,
    # price bars, news) and is light/fast, so it runs on the DEFAULT queue (NOT
    # the slow `deep` queue). Tue-Sat so it follows the deep run's days.
    "generate-signal-alerts": {
        "task": "app.tasks.signal_alerter.generate_signal_alerts",
        "schedule": crontab(hour=7, minute=0, day_of_week="tue-sat"),
        "options": {
            "expires": 3600,
        },
    },
    # Weekly cleanup: every Sunday at 2:00 AM IST
    "weekly-cleanup": {
        "task": "app.tasks.insight_calculator.cleanup_stale_data",
        "schedule": crontab(hour=2, minute=0, day_of_week="sunday"),
        "options": {
            "expires": 7200,
        },
    },
}


def is_market_hours() -> bool:
    """Check if the current time is within BSE market hours.

    Market hours: 9:15 AM - 3:30 PM IST, Monday through Friday.
    Returns True if within market hours, False otherwise.
    """
    from datetime import datetime

    import pytz

    ist = pytz.timezone("Asia/Kolkata")
    now = ist.localize(datetime.now()) if datetime.now().tzinfo is None else datetime.now().astimezone(ist)

    # Check weekday (Monday=0, Sunday=6)
    if now.weekday() >= 5:  # Saturday or Sunday
        return False

    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

    return market_open <= now <= market_close
