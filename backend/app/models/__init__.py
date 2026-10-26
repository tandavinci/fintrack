"""Database models package.

Import all models here so Alembic can discover them via Base.metadata.
"""

from backend.app.models.alert import Alert, AlertTypeEnum
from backend.app.models.data_snapshot import DataSnapshot
from backend.app.models.deep_memory import DeepMemoryEntry
from backend.app.models.education import DifficultyEnum, EducationModule, EducationProgress
from backend.app.models.insight import InsightScore, InsightSignalEnum
from backend.app.models.news_item import NewsItem
from backend.app.models.portfolio import Holding, Portfolio, Transaction, WatchlistItem
from backend.app.models.price_bar import PriceBar
from backend.app.models.recommendation import RecommendationRecord
from backend.app.models.signal_alert import SignalAlert
from backend.app.models.stock import PriceHistory, Sector, Stock, TrendData
from backend.app.models.user import User

__all__ = [
    "Alert",
    "AlertTypeEnum",
    "DataSnapshot",
    "DeepMemoryEntry",
    "DifficultyEnum",
    "EducationModule",
    "EducationProgress",
    "Holding",
    "InsightScore",
    "InsightSignalEnum",
    "NewsItem",
    "Portfolio",
    "PriceBar",
    "PriceHistory",
    "RecommendationRecord",
    "Sector",
    "SignalAlert",
    "Stock",
    "Transaction",
    "TrendData",
    "User",
    "WatchlistItem",
]
