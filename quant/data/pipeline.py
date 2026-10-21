"""Data pipeline that orchestrates fetching and enriching market data."""

from typing import Optional

import pandas as pd

from quant.config import Config
from quant.data.fetcher import DataFetcher
from quant.data.indicators import TechnicalIndicators


class DataPipeline:
    """
    Orchestrates the full data pipeline:
    1. Fetch raw OHLCV data (daily or intraday, for stocks/forex/commodities)
    2. Enrich with technical indicators (interval-tuned periods)
    3. Provide data slices for agents and backtesting
    """

    def __init__(self, ticker: str = None, interval: str = None, asset_class: str = None):
        self.interval = interval or Config.DEFAULT_INTERVAL
        self.asset_class = asset_class or Config.DEFAULT_ASSET_CLASS
        self.fetcher = DataFetcher(ticker, interval=self.interval, asset_class=self.asset_class)
        self.indicators = TechnicalIndicators()
        self._cache: Optional[pd.DataFrame] = None

    @property
    def ticker(self) -> str:
        return self.fetcher.ticker

    @property
    def display_ticker(self) -> str:
        return self.fetcher.display_ticker

    def get_enriched_data(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback_days: int = None,
        interval: str = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch and enrich data with all technical indicators.

        Indicator periods are automatically tuned for the interval:
        - 5m data: wider windows (50/200 SMA) to smooth noise
        - 30m/1d: standard windows (20/50 SMA)

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            lookback_days: Days to look back from end_date.
            interval: Override interval for this call.
            use_cache: Whether to return cached data if available.

        Returns:
            DataFrame with OHLCV + all technical indicators.
        """
        if use_cache and self._cache is not None:
            return self._cache

        fetch_interval = interval or self.interval

        raw_df = self.fetcher.fetch_historical(
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            interval=fetch_interval,
        )

        # Pass interval to indicators for period tuning
        enriched_df = self.indicators.add_all(raw_df, interval=fetch_interval)

        # Determine the long SMA column name for dropna
        periods = Config.get_indicator_periods(fetch_interval)
        long_sma_col = f"SMA_{periods['sma_long']}"

        # Drop rows where indicators haven't warmed up yet
        dropna_cols = [long_sma_col, "RSI"] if long_sma_col in enriched_df.columns else ["RSI"]
        enriched_df = enriched_df.dropna(subset=dropna_cols)

        self._cache = enriched_df
        return enriched_df

    def get_slice(
        self, df: pd.DataFrame, end_idx: int, lookback: int = 50
    ) -> pd.DataFrame:
        """
        Get a slice of data up to (and including) end_idx with a lookback window.
        Used in backtesting to simulate "what the agent sees" at a point in time.

        Args:
            df: Full enriched DataFrame.
            end_idx: The last index position to include (exclusive of future).
            lookback: Number of bars (periods) the agent gets to see.

        Returns:
            Sliced DataFrame.
        """
        start_idx = max(0, end_idx - lookback)
        return df.iloc[start_idx:end_idx].copy()

    def get_future_slice(
        self, df: pd.DataFrame, start_idx: int, horizon: int = 2
    ) -> pd.DataFrame:
        """
        Get the future data after a prediction point (for verification).

        Args:
            df: Full enriched DataFrame.
            start_idx: The index position where the prediction starts.
            horizon: Number of future periods to reveal.

        Returns:
            Sliced DataFrame of the actual future.
        """
        end_idx = min(len(df), start_idx + horizon)
        return df.iloc[start_idx:end_idx].copy()

    def get_latest_enriched(self, interval: str = None) -> pd.DataFrame:
        """Get the most recent data enriched with indicators (for live use)."""
        fetch_interval = interval or self.interval
        raw_df = self.fetcher.fetch_latest(interval=fetch_interval)
        enriched_df = self.indicators.add_all(raw_df, interval=fetch_interval)

        periods = Config.get_indicator_periods(fetch_interval)
        long_sma_col = f"SMA_{periods['sma_long']}"
        dropna_cols = [long_sma_col, "RSI"] if long_sma_col in enriched_df.columns else ["RSI"]
        enriched_df = enriched_df.dropna(subset=dropna_cols)
        return enriched_df

    def get_indicator_summary(self, df: pd.DataFrame = None) -> dict:
        """Get a text-friendly summary of the latest indicators."""
        if df is None:
            df = self.get_latest_enriched()
        return self.indicators.summarize(df)

    def clear_cache(self):
        """Clear cached data to force a fresh fetch."""
        self._cache = None
