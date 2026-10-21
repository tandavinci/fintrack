"""Data fetcher module for pulling historical and real-time market data."""

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from quant.config import Config


class DataFetcher:
    """Fetches OHLCV candlestick data from Yahoo Finance for stocks, forex, and commodities."""

    def __init__(
        self,
        ticker: str = None,
        interval: str = None,
        asset_class: str = None,
    ):
        self.asset_class = asset_class or Config.DEFAULT_ASSET_CLASS
        raw_ticker = ticker or Config.DEFAULT_TICKER
        # Resolve ticker to yfinance-compatible symbol (appends =X or =F as needed)
        self.ticker = Config.resolve_ticker(raw_ticker, self.asset_class)
        self.display_ticker = raw_ticker.upper().strip()  # Human-readable name
        self.interval = interval or Config.DEFAULT_INTERVAL

    def fetch_historical(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback_days: int = None,
        interval: str = None,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data at the configured interval.

        Automatically enforces the 60-day yfinance cap for intraday intervals
        (5m, 15m, 30m, etc.).

        Args:
            start_date: Start date string (YYYY-MM-DD). If None, uses lookback_days.
            end_date: End date string (YYYY-MM-DD). If None, uses today.
            lookback_days: Number of calendar days to look back from end_date.
            interval: Override interval (e.g., '5m', '30m', '1h', '1d').

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume.
            Index is timezone-naive DatetimeIndex.

        Raises:
            ValueError: If no data is returned from yfinance.
        """
        interval = interval or self.interval

        if lookback_days is None:
            lookback_days = Config.DEFAULT_LOOKBACK_DAYS

        # Enforce yfinance intraday constraint: max 60 days for sub-daily intervals
        if Config.is_intraday(interval):
            max_days = Config.INTRADAY_MAX_LOOKBACK_DAYS
            if lookback_days > max_days:
                lookback_days = max_days

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        if start_date is None:
            start_dt = datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=lookback_days)
            start_date = start_dt.strftime("%Y-%m-%d")

        stock = yf.Ticker(self.ticker)

        # For intraday intervals, yfinance requires 'period' instead of start/end
        # when going beyond ~7 days. Use period-based fetch for reliability.
        if Config.is_intraday(interval):
            period_str = f"{lookback_days}d"
            df = stock.history(period=period_str, interval=interval)
        else:
            df = stock.history(start=start_date, end=end_date, interval=interval)

        if df.empty:
            raise ValueError(
                f"No data returned for {self.ticker} ({self.display_ticker}, "
                f"asset_class={self.asset_class}, interval={interval}, lookback={lookback_days}d). "
                f"Verify the ticker is valid for this asset class."
            )

        # Standardize: keep only OHLCV
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

        # Remove timezone but preserve full datetime for intraday
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        # Name index appropriately
        df.index.name = "Datetime"

        return df

    def fetch_latest(self, period: str = None, interval: str = None) -> pd.DataFrame:
        """
        Fetch the most recent data for live/near-real-time use.

        Args:
            period: yfinance period string. Defaults to '60d' for intraday, '3mo' for daily.
            interval: Override interval.

        Returns:
            DataFrame with OHLCV data.
        """
        interval = interval or self.interval

        if period is None:
            period = "60d" if Config.is_intraday(interval) else "3mo"

        stock = yf.Ticker(self.ticker)
        df = stock.history(period=period, interval=interval)

        if df.empty:
            raise ValueError(
                f"No recent data returned for {self.ticker} "
                f"({self.display_ticker}, asset_class={self.asset_class}, interval={interval})"
            )

        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        df.index.name = "Datetime"
        return df
