"""Technical indicator calculations for market analysis.

Indicator rolling windows are expressed in "periods" (not days).
For 5m data, a 50-period SMA represents ~4 hours of price history.
For 30m data, a 20-period SMA represents ~10 hours.
For daily data, a 20-period SMA represents 20 trading days.

The `add_all()` method accepts an optional `interval` parameter to
automatically select appropriate window sizes per the INDICATOR_PERIODS config.
"""

import numpy as np
import pandas as pd

from quant.config import Config


class TechnicalIndicators:
    """Computes standard technical indicators on OHLCV DataFrames."""

    @staticmethod
    def add_all(df: pd.DataFrame, interval: str = None) -> pd.DataFrame:
        """
        Add all standard technical indicators to the DataFrame.

        Uses interval-specific period tuning: 5m data gets wider windows
        (50/200 SMA) to smooth intraday noise.

        Args:
            df: DataFrame with Open, High, Low, Close, Volume columns.
            interval: Candle interval (e.g., '5m', '30m', '1d') for period tuning.

        Returns:
            DataFrame with indicator columns appended.
        """
        periods = Config.get_indicator_periods(interval) if interval else Config.get_indicator_periods("1d")

        df = df.copy()
        df = TechnicalIndicators.add_moving_averages(
            df, short=periods["sma_short"], long=periods["sma_long"]
        )
        df = TechnicalIndicators.add_rsi(df, period=periods["rsi_period"])
        df = TechnicalIndicators.add_macd(df)
        df = TechnicalIndicators.add_bollinger_bands(df, period=periods["sma_short"])
        df = TechnicalIndicators.add_atr(df, period=periods["atr_period"])
        df = TechnicalIndicators.add_volume_sma(df, period=periods["sma_short"])
        return df

    @staticmethod
    def add_moving_averages(
        df: pd.DataFrame, short: int = 20, long: int = 50
    ) -> pd.DataFrame:
        """Add Simple Moving Averages (SMA)."""
        df = df.copy()
        df[f"SMA_{short}"] = df["Close"].rolling(window=short).mean()
        df[f"SMA_{long}"] = df["Close"].rolling(window=long).mean()
        df["EMA_12"] = df["Close"].ewm(span=12, adjust=False).mean()
        df["EMA_26"] = df["Close"].ewm(span=26, adjust=False).mean()
        return df

    @staticmethod
    def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Add Relative Strength Index (RSI)."""
        df = df.copy()
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()

        # Use exponential smoothing after initial SMA
        for i in range(period, len(df)):
            avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
            avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

        rs = avg_gain / avg_loss
        df["RSI"] = 100 - (100 / (1 + rs))
        return df

    @staticmethod
    def add_macd(
        df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> pd.DataFrame:
        """Add MACD (Moving Average Convergence Divergence)."""
        df = df.copy()
        ema_fast = df["Close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["Close"].ewm(span=slow, adjust=False).mean()

        df["MACD"] = ema_fast - ema_slow
        df["MACD_Signal"] = df["MACD"].ewm(span=signal, adjust=False).mean()
        df["MACD_Histogram"] = df["MACD"] - df["MACD_Signal"]
        return df

    @staticmethod
    def add_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
        """Add Bollinger Bands."""
        df = df.copy()
        sma = df["Close"].rolling(window=period).mean()
        std = df["Close"].rolling(window=period).std()

        df["BB_Upper"] = sma + (std_dev * std)
        df["BB_Middle"] = sma
        df["BB_Lower"] = sma - (std_dev * std)
        df["BB_Width"] = (df["BB_Upper"] - df["BB_Lower"]) / df["BB_Middle"]
        return df

    @staticmethod
    def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Add Average True Range (ATR) for volatility measurement."""
        df = df.copy()
        high_low = df["High"] - df["Low"]
        high_close_prev = abs(df["High"] - df["Close"].shift(1))
        low_close_prev = abs(df["Low"] - df["Close"].shift(1))

        true_range = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
        df["ATR"] = true_range.rolling(window=period).mean()
        return df

    @staticmethod
    def add_volume_sma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """Add Volume SMA for volume analysis."""
        df = df.copy()
        df["Volume_SMA"] = df["Volume"].rolling(window=period).mean()
        df["Volume_Ratio"] = df["Volume"] / df["Volume_SMA"]
        return df

    @staticmethod
    def summarize(df: pd.DataFrame) -> dict:
        """
        Create a text summary of the latest indicator readings.
        Useful for passing to LLM agents as context.

        Args:
            df: DataFrame with indicators already added.

        Returns:
            Dictionary with latest indicator values.
        """
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest

        summary = {
            "price": {
                "current": round(float(latest["Close"]), 2),
                "change_pct": round(
                    float((latest["Close"] - prev["Close"]) / prev["Close"] * 100), 2
                ),
                "high": round(float(latest["High"]), 2),
                "low": round(float(latest["Low"]), 2),
            },
            "moving_averages": {},
            "momentum": {},
            "volatility": {},
            "volume": {},
        }

        # Moving averages
        for col in ["SMA_20", "SMA_50", "EMA_12", "EMA_26"]:
            if col in df.columns and not pd.isna(latest[col]):
                summary["moving_averages"][col] = round(float(latest[col]), 2)

        # Momentum
        if "RSI" in df.columns and not pd.isna(latest["RSI"]):
            summary["momentum"]["RSI"] = round(float(latest["RSI"]), 2)
        if "MACD" in df.columns and not pd.isna(latest["MACD"]):
            summary["momentum"]["MACD"] = round(float(latest["MACD"]), 4)
            summary["momentum"]["MACD_Signal"] = round(float(latest["MACD_Signal"]), 4)
            summary["momentum"]["MACD_Histogram"] = round(float(latest["MACD_Histogram"]), 4)

        # Volatility
        if "ATR" in df.columns and not pd.isna(latest["ATR"]):
            summary["volatility"]["ATR"] = round(float(latest["ATR"]), 2)
        if "BB_Width" in df.columns and not pd.isna(latest["BB_Width"]):
            summary["volatility"]["BB_Width"] = round(float(latest["BB_Width"]), 4)
            summary["volatility"]["BB_Upper"] = round(float(latest["BB_Upper"]), 2)
            summary["volatility"]["BB_Lower"] = round(float(latest["BB_Lower"]), 2)

        # Volume
        if "Volume_Ratio" in df.columns and not pd.isna(latest["Volume_Ratio"]):
            summary["volume"]["Volume_Ratio"] = round(float(latest["Volume_Ratio"]), 2)
            summary["volume"]["Volume"] = int(latest["Volume"])

        return summary
