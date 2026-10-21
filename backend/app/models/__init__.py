"""Database models package.

Import all models here so Alembic can discover them via Base.metadata.
"""

from backend.app.models.alert import Alert, AlertTypeEnum
from backend.app.models.education import DifficultyEnum, EducationModule, EducationProgress
from backend.app.models.insight import InsightScore, InsightSignalEnum
from backend.app.models.portfolio import Holding, Portfolio, Transaction, WatchlistItem
from backend.app.models.stock import PriceHistory, Sector, Stock, TrendData
from backend.app.models.user import User

__all__ = [
    "Alert",
    "AlertTypeEnum",
    "DifficultyEnum",
    "EducationModule",
    "EducationProgress",
    "Holding",
    "InsightScore",
    "InsightSignalEnum",
    "Portfolio",
    "PriceHistory",
    "Sector",
    "Stock",
    "Transaction",
    "TrendData",
    "User",
    "WatchlistItem",
]
