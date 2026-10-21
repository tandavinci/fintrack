"""Configuration management for the quant engine."""


class Config:
    """Central configuration for the trading system."""

    # Data settings
    DEFAULT_TICKER: str = "SPY"
    DEFAULT_LOOKBACK_DAYS: int = 120
    DEFAULT_PREDICTION_HORIZON: int = 2  # periods (2 x 30m = 1 hour)

    # Interval settings
    DEFAULT_INTERVAL: str = "30m"  # Default candlestick interval
    # yfinance maximum lookback for intraday intervals
    INTRADAY_MAX_LOOKBACK_DAYS: int = 60
    # Intervals considered "intraday" (subject to 60-day limit)
    INTRADAY_INTERVALS: list = ["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"]

    # --- Asset Class Configuration ---
    ASSET_CLASSES: list = ["stock", "forex", "commodity"]
    DEFAULT_ASSET_CLASS: str = "stock"

    # Forex ticker suffixes (yfinance requires =X)
    FOREX_PAIRS: dict = {
        "EURUSD": "EURUSD=X",
        "GBPUSD": "GBPUSD=X",
        "USDJPY": "USDJPY=X",
        "AUDUSD": "AUDUSD=X",
        "USDCAD": "USDCAD=X",
        "USDCHF": "USDCHF=X",
        "NZDUSD": "NZDUSD=X",
        "EURGBP": "EURGBP=X",
        "EURJPY": "EURJPY=X",
        "GBPJPY": "GBPJPY=X",
        "AUDJPY": "AUDJPY=X",
        "EURAUD": "EURAUD=X",
    }

    # Commodity ticker suffixes (yfinance requires =F)
    COMMODITY_TICKERS: dict = {
        "GOLD": "GC=F",
        "SILVER": "SI=F",
        "OIL": "CL=F",
        "CRUDE": "CL=F",
        "NATURAL_GAS": "NG=F",
        "COPPER": "HG=F",
        "PLATINUM": "PL=F",
        "PALLADIUM": "PA=F",
        "WHEAT": "ZW=F",
        "CORN": "ZC=F",
        "SOYBEANS": "ZS=F",
        "COTTON": "CT=F",
    }

    # Default tickers per asset class (for frontend dropdowns — 10+ each)
    DEFAULT_TICKERS: dict = {
        "stock": [
            "SPY", "AAPL", "MSFT", "GOOGL", "AMZN",
            "TSLA", "NVDA", "META", "NFLX", "AMD",
        ],
        "forex": [
            "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
            "USDCHF", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY",
        ],
        "commodity": [
            "GOLD", "SILVER", "OIL", "NATURAL_GAS", "COPPER",
            "PLATINUM", "PALLADIUM", "WHEAT", "CORN", "SOYBEANS",
        ],
    }

    # --- Indicator Tuning per Interval ---
    # 5m data is very noisy; use wider MA windows to smooth
    INDICATOR_PERIODS: dict = {
        "5m": {"sma_short": 50, "sma_long": 200, "rsi_period": 21, "atr_period": 21},
        "15m": {"sma_short": 30, "sma_long": 100, "rsi_period": 14, "atr_period": 14},
        "30m": {"sma_short": 20, "sma_long": 50, "rsi_period": 14, "atr_period": 14},
        "1h": {"sma_short": 20, "sma_long": 50, "rsi_period": 14, "atr_period": 14},
        "1d": {"sma_short": 20, "sma_long": 50, "rsi_period": 14, "atr_period": 14},
    }

    # LLM Provider: "rule_based", "gateway"
    DEFAULT_LLM_PROVIDER: str = "rule_based"

    # Market Regime settings
    REGIME_LOOKBACK: int = 50  # bars for regime classification
    SHORT_MA_PERIOD: int = 20
    LONG_MA_PERIOD: int = 50

    # Strategy Agent settings
    LLM_TEMPERATURE: float = 0.1

    # Backtesting settings
    BACKTEST_TRAIN_RATIO: float = 0.7  # 70% train, 30% test
    MAX_LESSONS_IN_MEMORY: int = 20

    @staticmethod
    def is_intraday(interval: str) -> bool:
        """Check if an interval is intraday (subject to 60-day yfinance limit)."""
        return interval in Config.INTRADAY_INTERVALS

    @staticmethod
    def get_max_lookback_days(interval: str) -> int:
        """Get the maximum lookback days allowed for a given interval."""
        if Config.is_intraday(interval):
            return Config.INTRADAY_MAX_LOOKBACK_DAYS
        return Config.DEFAULT_LOOKBACK_DAYS

    @staticmethod
    def get_periods_per_day(interval: str) -> float:
        """
        Get the approximate number of periods per trading day for a given interval.
        Assumes 6.5 trading hours per day (equities).
        Forex trades ~24h so this is approximate for forex.
        """
        minutes_map = {
            "1m": 1, "2m": 2, "5m": 5, "15m": 15,
            "30m": 30, "60m": 60, "90m": 90, "1h": 60,
            "1d": 390,  # Full day
        }
        minutes = minutes_map.get(interval, 390)
        if interval == "1d":
            return 1.0
        return 390.0 / minutes  # 6.5 hours = 390 minutes

    @staticmethod
    def get_periods_per_year(interval: str) -> float:
        """Get annualization factor (periods per year) for Sharpe ratio."""
        return Config.get_periods_per_day(interval) * 252

    @staticmethod
    def resolve_ticker(ticker: str, asset_class: str = "stock") -> str:
        """
        Resolve a human-friendly ticker to a yfinance-compatible symbol.

        - Forex: appends =X if not already present (e.g., EURUSD -> EURUSD=X)
        - Commodity: maps friendly names to futures tickers (e.g., GOLD -> GC=F)
        - Stock: returns as-is

        Args:
            ticker: User-provided ticker string.
            asset_class: One of 'stock', 'forex', 'commodity'.

        Returns:
            yfinance-compatible ticker string.
        """
        ticker_upper = ticker.upper().strip()

        if asset_class == "forex":
            # Check mapping first
            if ticker_upper in Config.FOREX_PAIRS:
                return Config.FOREX_PAIRS[ticker_upper]
            # Already has =X suffix
            if ticker_upper.endswith("=X"):
                return ticker_upper
            # Append =X
            return f"{ticker_upper}=X"

        elif asset_class == "commodity":
            # Check friendly name mapping
            if ticker_upper in Config.COMMODITY_TICKERS:
                return Config.COMMODITY_TICKERS[ticker_upper]
            # Already has =F suffix
            if ticker_upper.endswith("=F"):
                return ticker_upper
            # Append =F as fallback
            return f"{ticker_upper}=F"

        # Stock: return as-is
        return ticker_upper

    @staticmethod
    def get_indicator_periods(interval: str) -> dict:
        """
        Get the appropriate indicator periods for a given interval.
        5m data uses wider windows (50/200) to smooth noise.
        """
        return Config.INDICATOR_PERIODS.get(interval, Config.INDICATOR_PERIODS["1d"])
