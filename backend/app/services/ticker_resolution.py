"""Ticker resolution for the unified data service (design §4.3).

Thin wrapper over ``quant.config.Config.resolve_ticker`` that layers Indian-equity
(.BO / .NS) rules on top. Bare Indian symbols default to BSE (``.BO``; the golden
set is all ``.BO``); a symbol already carrying ``.BO``/``.NS`` passes through
unchanged. Forex/commodity/stock passthrough is delegated to the quant Config so
the resolution logic lives in ONE place (never forked). Resolution is idempotent:
``resolve(resolve(x)) == resolve(x)`` for every supported case.
"""

from quant.config import Config


def resolve_market_ticker(ticker: str, asset_class: str = "stock") -> str:
    """Resolve a human ticker to a yfinance-compatible symbol.

    Args:
        ticker: User/caller-provided ticker string (e.g. "RELIANCE" or "RELIANCE.BO").
        asset_class: One of ``Config.ASSET_CLASSES`` ("stock", "forex", "commodity").

    Returns:
        A yfinance-compatible symbol. For a bare Indian equity, ``.BO`` is appended.

    Raises:
        ValueError: If ``ticker`` is empty after strip.
    """
    if ticker is None or not ticker.strip():
        raise ValueError("ticker must be a non-empty string")

    t = ticker.upper().strip()

    if asset_class == "stock" and not t.endswith((".BO", ".NS")):
        # Bare symbol -> default to BSE (.BO); golden set is .BO. A symbol that
        # already carries .BO/.NS passes through unchanged (handled by the guard
        # above). US tickers are out of the golden path; resolution does not
        # guess .BO for known-US tickers (design A3) — bare stocks default .BO.
        t = f"{t}.BO"

    return Config.resolve_ticker(t, asset_class)
