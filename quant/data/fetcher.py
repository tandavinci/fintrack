"""Data fetcher module for pulling historical and real-time market data."""

from typing import Optional

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

        # Gate G2 seam (design §4.6): route the historical read through the ONE
        # unified MarketDataService instead of calling yfinance directly. The
        # service owns ticker resolution, window normalization (§4.4c, EXCLUSIVE
        # end), the Redis/Postgres read-cache, and the yfinance leg — reproducing
        # this method's former period-vs-start/end branch and OHLCV post-processing
        # byte-for-byte, so the return contract (OHLCV-only float64, tz-naive
        # Datetime-named index) is unchanged. ``fetch_latest`` is NOT re-pointed.
        from datetime import date as _date

        from backend.app.services.data_service import MarketDataService

        start = _date.fromisoformat(start_date) if start_date else None
        end = _date.fromisoformat(end_date) if end_date else None

        return MarketDataService().get_ohlcv_sync(
            ticker=self.display_ticker,
            interval=interval,
            start=start,
            end=end,
            lookback_days=lookback_days,
            asset_class=self.asset_class,
        )

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
