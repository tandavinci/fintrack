"""Database seed script for BSE Stock Investment Tracker.

Populates:
- BSE sectors (IT, Pharma, Banking, Energy, FMCG, Automotive, Metals, Telecom, Real Estate, Infrastructure)
- Sample stock data (top 30 BSE stocks with real BSE codes)
- Education modules (at least 2 per difficulty level covering required topics)
- Glossary terms are already in education_service.py (in-memory), no DB seeding needed

Usage:
    python seed_data.py

The script is idempotent — running it multiple times will not create duplicates.
"""

import asyncio
import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Add app to path for standalone execution
sys.path.insert(0, ".")

from backend.app.database import async_session_factory, engine
from backend.app.models.stock import Sector, Stock
from backend.app.models.education import EducationModule


# ─── Sector Data ───────────────────────────────────────────────────────────────

SECTORS = [
    {"name": "IT", "description": "Information Technology companies including software, services, and hardware"},
    {"name": "Pharma", "description": "Pharmaceutical and healthcare companies"},
    {"name": "Banking", "description": "Banking and financial services institutions"},
    {"name": "Energy", "description": "Oil, gas, and power generation companies"},
    {"name": "FMCG", "description": "Fast-Moving Consumer Goods companies"},
    {"name": "Automotive", "description": "Automobile manufacturers and auto component companies"},
    {"name": "Metals", "description": "Metals, mining, and steel manufacturing companies"},
    {"name": "Telecom", "description": "Telecommunications service providers"},
    {"name": "Real Estate", "description": "Real estate developers and property companies"},
    {"name": "Infrastructure", "description": "Infrastructure development and construction companies"},
]


# ─── Stock Data (Top 30 BSE Stocks) ───────────────────────────────────────────

STOCKS = [
    # IT Sector
    {"bse_code": "500325", "ticker_symbol": "RELIANCE", "company_name": "Reliance Industries Limited", "sector": "Energy", "current_price": Decimal("2945.50"), "market_cap": Decimal("1992500000000.00")},
    {"bse_code": "532540", "ticker_symbol": "TCS", "company_name": "Tata Consultancy Services Limited", "sector": "IT", "current_price": Decimal("3842.75"), "market_cap": Decimal("1398000000000.00")},
    {"bse_code": "500180", "ticker_symbol": "HDFCBANK", "company_name": "HDFC Bank Limited", "sector": "Banking", "current_price": Decimal("1625.30"), "market_cap": Decimal("1237000000000.00")},
    {"bse_code": "500209", "ticker_symbol": "INFY", "company_name": "Infosys Limited", "sector": "IT", "current_price": Decimal("1545.60"), "market_cap": Decimal("641000000000.00")},
    {"bse_code": "500696", "ticker_symbol": "HINDUNILVR", "company_name": "Hindustan Unilever Limited", "sector": "FMCG", "current_price": Decimal("2510.85"), "market_cap": Decimal("590000000000.00")},
    {"bse_code": "500112", "ticker_symbol": "SBIN", "company_name": "State Bank of India", "sector": "Banking", "current_price": Decimal("628.45"), "market_cap": Decimal("561000000000.00")},
    {"bse_code": "532454", "ticker_symbol": "BHARTIARTL", "company_name": "Bharti Airtel Limited", "sector": "Telecom", "current_price": Decimal("1485.20"), "market_cap": Decimal("885000000000.00")},
    {"bse_code": "500247", "ticker_symbol": "KOTAKBANK", "company_name": "Kotak Mahindra Bank Limited", "sector": "Banking", "current_price": Decimal("1785.40"), "market_cap": Decimal("354000000000.00")},
    {"bse_code": "532174", "ticker_symbol": "ICICIBANK", "company_name": "ICICI Bank Limited", "sector": "Banking", "current_price": Decimal("1098.75"), "market_cap": Decimal("770000000000.00")},
    {"bse_code": "500875", "ticker_symbol": "ITC", "company_name": "ITC Limited", "sector": "FMCG", "current_price": Decimal("445.30"), "market_cap": Decimal("556000000000.00")},
    # Pharma
    {"bse_code": "500124", "ticker_symbol": "DRREDDY", "company_name": "Dr. Reddy's Laboratories Limited", "sector": "Pharma", "current_price": Decimal("5680.50"), "market_cap": Decimal("946000000000.00")},
    {"bse_code": "524715", "ticker_symbol": "SUNPHARMA", "company_name": "Sun Pharmaceutical Industries Limited", "sector": "Pharma", "current_price": Decimal("1245.80"), "market_cap": Decimal("299000000000.00")},
    {"bse_code": "500257", "ticker_symbol": "LUPIN", "company_name": "Lupin Limited", "sector": "Pharma", "current_price": Decimal("1580.25"), "market_cap": Decimal("360000000000.00")},
    # Automotive
    {"bse_code": "532500", "ticker_symbol": "MARUTI", "company_name": "Maruti Suzuki India Limited", "sector": "Automotive", "current_price": Decimal("10845.60"), "market_cap": Decimal("327000000000.00")},
    {"bse_code": "500570", "ticker_symbol": "TATAMOTORS", "company_name": "Tata Motors Limited", "sector": "Automotive", "current_price": Decimal("945.30"), "market_cap": Decimal("348000000000.00")},
    {"bse_code": "532977", "ticker_symbol": "BAJAJ-AUTO", "company_name": "Bajaj Auto Limited", "sector": "Automotive", "current_price": Decimal("7250.40"), "market_cap": Decimal("210000000000.00")},
    # Metals
    {"bse_code": "500470", "ticker_symbol": "TATASTEEL", "company_name": "Tata Steel Limited", "sector": "Metals", "current_price": Decimal("132.45"), "market_cap": Decimal("161000000000.00")},
    {"bse_code": "500440", "ticker_symbol": "HINDALCO", "company_name": "Hindalco Industries Limited", "sector": "Metals", "current_price": Decimal("520.75"), "market_cap": Decimal("117000000000.00")},
    {"bse_code": "500387", "ticker_symbol": "JSWSTEEL", "company_name": "JSW Steel Limited", "sector": "Metals", "current_price": Decimal("785.90"), "market_cap": Decimal("190000000000.00")},
    # Energy
    {"bse_code": "500312", "ticker_symbol": "ONGC", "company_name": "Oil and Natural Gas Corporation Limited", "sector": "Energy", "current_price": Decimal("272.35"), "market_cap": Decimal("342000000000.00")},
    {"bse_code": "530965", "ticker_symbol": "IOC", "company_name": "Indian Oil Corporation Limited", "sector": "Energy", "current_price": Decimal("165.80"), "market_cap": Decimal("234000000000.00")},
    {"bse_code": "539254", "ticker_symbol": "NTPC", "company_name": "NTPC Limited", "sector": "Energy", "current_price": Decimal("355.60"), "market_cap": Decimal("345000000000.00")},
    # Infrastructure
    {"bse_code": "500510", "ticker_symbol": "LT", "company_name": "Larsen & Toubro Limited", "sector": "Infrastructure", "current_price": Decimal("3420.80"), "market_cap": Decimal("470000000000.00")},
    {"bse_code": "532921", "ticker_symbol": "ADANIPORTS", "company_name": "Adani Ports and Special Economic Zone Limited", "sector": "Infrastructure", "current_price": Decimal("1185.45"), "market_cap": Decimal("256000000000.00")},
    # Telecom
    {"bse_code": "532822", "ticker_symbol": "IDEA", "company_name": "Vodafone Idea Limited", "sector": "Telecom", "current_price": Decimal("14.25"), "market_cap": Decimal("70000000000.00")},
    # Real Estate
    {"bse_code": "532538", "ticker_symbol": "DLF", "company_name": "DLF Limited", "sector": "Real Estate", "current_price": Decimal("785.60"), "market_cap": Decimal("194000000000.00")},
    {"bse_code": "543216", "ticker_symbol": "LODHA", "company_name": "Macrotech Developers Limited", "sector": "Real Estate", "current_price": Decimal("1245.30"), "market_cap": Decimal("120000000000.00")},
    # More IT
    {"bse_code": "532286", "ticker_symbol": "WIPRO", "company_name": "Wipro Limited", "sector": "IT", "current_price": Decimal("452.80"), "market_cap": Decimal("236000000000.00")},
    {"bse_code": "532281", "ticker_symbol": "HCLTECH", "company_name": "HCL Technologies Limited", "sector": "IT", "current_price": Decimal("1380.50"), "market_cap": Decimal("375000000000.00")},
    {"bse_code": "532478", "ticker_symbol": "TECHM", "company_name": "Tech Mahindra Limited", "sector": "IT", "current_price": Decimal("1285.40"), "market_cap": Decimal("125000000000.00")},
]


# ─── Education Module Data ─────────────────────────────────────────────────────

EDUCATION_MODULES = [
    # BEGINNER modules
    {
        "title": "Introduction to Candlestick Patterns",
        "content": (
            "# Introduction to Candlestick Patterns\n\n"
            "Candlestick charts are one of the most popular ways to visualize stock price movements. "
            "Each candlestick represents price action over a specific time period.\n\n"
            "## Anatomy of a Candlestick\n"
            "- **Body**: The thick part showing the range between open and close prices\n"
            "- **Wick/Shadow**: The thin lines showing the high and low of the period\n"
            "- **Bullish (Green)**: Close price is higher than open price\n"
            "- **Bearish (Red)**: Close price is lower than open price\n\n"
            "## Basic Patterns\n"
            "- **Doji**: Open and close are nearly equal — indicates indecision\n"
            "- **Hammer**: Small body at the top with long lower wick — potential reversal signal\n"
            "- **Engulfing**: A candle that completely covers the previous candle's body\n\n"
            "Understanding these patterns helps identify potential entry and exit points."
        ),
        "difficulty": "BEGINNER",
        "category": "candlestick_patterns",
        "sector_relevance": None,
        "order_index": 1,
    },
    {
        "title": "Understanding Moving Averages",
        "content": (
            "# Understanding Moving Averages\n\n"
            "A moving average (MA) smooths out price data by calculating the average price over a "
            "specified number of periods. It helps identify the direction of the trend.\n\n"
            "## Types of Moving Averages\n"
            "- **Simple Moving Average (SMA)**: Average of closing prices over N periods\n"
            "- **Exponential Moving Average (EMA)**: Gives more weight to recent prices\n\n"
            "## Common Periods\n"
            "- **20-day MA**: Short-term trend\n"
            "- **50-day MA**: Medium-term trend\n"
            "- **200-day MA**: Long-term trend\n\n"
            "## How to Use\n"
            "- Price above MA → Bullish trend\n"
            "- Price below MA → Bearish trend\n"
            "- MA crossovers can signal trend changes (e.g., Golden Cross, Death Cross)"
        ),
        "difficulty": "BEGINNER",
        "category": "moving_averages",
        "sector_relevance": None,
        "order_index": 2,
    },
    {
        "title": "What is Diversification?",
        "content": (
            "# What is Diversification?\n\n"
            "Diversification is a risk management strategy that involves spreading investments "
            "across different assets to reduce exposure to any single risk.\n\n"
            "## Why Diversify?\n"
            "- Reduces overall portfolio risk\n"
            "- Protects against sector-specific downturns\n"
            "- Smooths out returns over time\n\n"
            "## Ways to Diversify\n"
            "- **Across sectors**: IT, Pharma, Banking, FMCG, etc.\n"
            "- **Across market caps**: Large-cap, mid-cap, small-cap\n"
            "- **Across asset classes**: Stocks, bonds, gold, real estate\n\n"
            "## BSE Context\n"
            "The BSE has 10+ sectors. Holding stocks from at least 3-4 different sectors "
            "provides meaningful diversification for Indian investors."
        ),
        "difficulty": "BEGINNER",
        "category": "diversification",
        "sector_relevance": None,
        "order_index": 3,
    },
    # INTERMEDIATE modules
    {
        "title": "Support and Resistance Levels",
        "content": (
            "# Support and Resistance Levels\n\n"
            "Support and resistance are key concepts in technical analysis that identify price "
            "levels where buying or selling pressure tends to be strongest.\n\n"
            "## Support\n"
            "- A price level where downward movement tends to pause\n"
            "- Buyers become active at this level, creating a 'floor'\n"
            "- The more times a support level is tested, the stronger it becomes\n\n"
            "## Resistance\n"
            "- A price level where upward movement tends to pause\n"
            "- Sellers become active, creating a 'ceiling'\n"
            "- Once broken, resistance often becomes new support\n\n"
            "## Identifying Levels\n"
            "- Look for price clusters on historical charts\n"
            "- Round numbers often act as psychological support/resistance\n"
            "- Moving averages can serve as dynamic support/resistance\n\n"
            "## Trading Implications\n"
            "- Buy near support with stop-loss below\n"
            "- Sell near resistance or wait for breakout confirmation"
        ),
        "difficulty": "INTERMEDIATE",
        "category": "support_resistance",
        "sector_relevance": None,
        "order_index": 4,
    },
    {
        "title": "Trend Analysis and Identification",
        "content": (
            "# Trend Analysis and Identification\n\n"
            "Trend analysis is the practice of identifying the general direction of a "
            "stock's price movement over time.\n\n"
            "## Types of Trends\n"
            "- **Uptrend**: Series of higher highs and higher lows\n"
            "- **Downtrend**: Series of lower highs and lower lows\n"
            "- **Sideways/Range**: Price moves within a horizontal band\n\n"
            "## Tools for Trend Analysis\n"
            "- **Trendlines**: Draw lines connecting successive lows (uptrend) or highs (downtrend)\n"
            "- **Moving Averages**: 50-day and 200-day MAs indicate medium/long-term trends\n"
            "- **Google Trends**: Rising search interest can precede price movements\n\n"
            "## Trend Strength\n"
            "- Volume confirms trends: rising volume with rising price = strong trend\n"
            "- Trend Score (0-100) combines price momentum with public interest data\n\n"
            "## Practical Application\n"
            "- Trade in the direction of the trend\n"
            "- Use pullbacks within an uptrend as buying opportunities\n"
            "- Be cautious of trend reversals signaled by divergences"
        ),
        "difficulty": "INTERMEDIATE",
        "category": "trend_analysis",
        "sector_relevance": None,
        "order_index": 5,
    },
    {
        "title": "Introduction to Risk Management",
        "content": (
            "# Introduction to Risk Management\n\n"
            "Risk management is the process of identifying, assessing, and controlling threats "
            "to your investment capital.\n\n"
            "## Key Principles\n"
            "- Never invest more than you can afford to lose\n"
            "- Use position sizing to limit exposure per trade\n"
            "- Set stop-loss orders to limit downside\n\n"
            "## Position Sizing\n"
            "- **2% Rule**: Never risk more than 2% of portfolio on a single trade\n"
            "- Calculate position size: (Portfolio × Risk%) / (Entry - Stop-loss)\n\n"
            "## Types of Risk\n"
            "- **Market Risk**: Overall market decline\n"
            "- **Sector Risk**: Specific sector downturn\n"
            "- **Company Risk**: Individual company issues\n"
            "- **Liquidity Risk**: Inability to exit at desired price\n\n"
            "## Risk Mitigation Strategies\n"
            "- Diversification across sectors and market caps\n"
            "- Regular portfolio rebalancing\n"
            "- Using alerts for price thresholds\n"
            "- Monitoring Trend Scores for early warning signals"
        ),
        "difficulty": "INTERMEDIATE",
        "category": "risk_management",
        "sector_relevance": None,
        "order_index": 6,
    },
    # ADVANCED modules
    {
        "title": "Advanced Candlestick Patterns and Confirmations",
        "content": (
            "# Advanced Candlestick Patterns and Confirmations\n\n"
            "Advanced candlestick analysis combines multi-candle patterns with volume "
            "and other technical indicators for higher-probability signals.\n\n"
            "## Multi-Candle Patterns\n"
            "- **Morning Star**: Three-candle bullish reversal (bearish, doji, bullish)\n"
            "- **Evening Star**: Three-candle bearish reversal (bullish, doji, bearish)\n"
            "- **Three White Soldiers**: Three consecutive bullish candles with rising closes\n"
            "- **Three Black Crows**: Three consecutive bearish candles with falling closes\n\n"
            "## Pattern Confirmation\n"
            "- Volume should increase on breakout candles\n"
            "- Confirm with RSI or MACD divergence\n"
            "- Patterns near support/resistance are more reliable\n\n"
            "## Context Matters\n"
            "- Patterns in strong trends are continuation signals\n"
            "- Patterns at extremes (overbought/oversold) are reversal signals\n"
            "- BSE stocks with high volume show more reliable patterns\n\n"
            "## Combining with Trend Score\n"
            "- Rising Trend Score + bullish pattern = higher conviction buy\n"
            "- Falling Trend Score + bearish pattern = stronger sell signal"
        ),
        "difficulty": "ADVANCED",
        "category": "candlestick_patterns",
        "sector_relevance": None,
        "order_index": 7,
    },
    {
        "title": "Advanced Moving Average Strategies",
        "content": (
            "# Advanced Moving Average Strategies\n\n"
            "Beyond basic crossover signals, moving averages can be used in sophisticated "
            "trading strategies combining multiple timeframes and indicators.\n\n"
            "## Golden Cross and Death Cross\n"
            "- **Golden Cross**: 50-day MA crosses above 200-day MA (strong bullish signal)\n"
            "- **Death Cross**: 50-day MA crosses below 200-day MA (strong bearish signal)\n"
            "- These signals work best on liquid BSE large-caps\n\n"
            "## Moving Average Envelopes\n"
            "- Plot bands at fixed percentage above/below the MA\n"
            "- Price touching the upper band = overbought\n"
            "- Price touching the lower band = oversold\n\n"
            "## Triple MA Strategy\n"
            "- Use 20-day (fast), 50-day (medium), 200-day (slow)\n"
            "- All three aligned upward = strongest bullish signal\n"
            "- Fast crosses below medium while slow is above = pullback opportunity\n\n"
            "## Integration with Trend Analysis\n"
            "- Combine MA signals with Google Trends data\n"
            "- Rising public interest (Trend Score > 60) + Golden Cross = high-conviction entry\n"
            "- Use sector-level MAs to identify rotating market leadership"
        ),
        "difficulty": "ADVANCED",
        "category": "moving_averages",
        "sector_relevance": None,
        "order_index": 8,
    },
    {
        "title": "Portfolio Risk Management and Optimization",
        "content": (
            "# Portfolio Risk Management and Optimization\n\n"
            "Advanced risk management goes beyond simple diversification to optimize "
            "the risk-reward profile of your entire portfolio.\n\n"
            "## Risk Metrics\n"
            "- **Beta**: Stock's sensitivity to market movements (Sensex)\n"
            "- **Standard Deviation**: Volatility measure of returns\n"
            "- **Maximum Drawdown**: Largest peak-to-trough decline\n"
            "- **Sharpe Ratio**: Risk-adjusted return measurement\n\n"
            "## Portfolio Optimization\n"
            "- Balance high-beta (aggressive) and low-beta (defensive) stocks\n"
            "- Rebalance quarterly or when allocation drifts > 5%\n"
            "- Use correlation analysis to find stocks that move independently\n\n"
            "## Sector Rotation Strategy\n"
            "- Monitor sector Trend Scores to identify momentum shifts\n"
            "- Overweight sectors with rising Trend Scores\n"
            "- Underweight sectors showing declining interest\n\n"
            "## Indian Market Considerations\n"
            "- Banking sector often leads broader market movements\n"
            "- IT sector correlates with USD/INR movement\n"
            "- FMCG provides defensive stability during corrections\n"
            "- Use BSE sector indices for benchmark comparison"
        ),
        "difficulty": "ADVANCED",
        "category": "risk_management",
        "sector_relevance": None,
        "order_index": 9,
    },
]


# ─── Seed Functions ────────────────────────────────────────────────────────────


async def seed_sectors(db: AsyncSession) -> dict[str, int]:
    """Seed BSE sectors. Returns a mapping of sector name to sector ID."""
    sector_map: dict[str, int] = {}

    for sector_data in SECTORS:
        # Check if sector already exists
        stmt = select(Sector).where(Sector.name == sector_data["name"])
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            sector_map[existing.name] = existing.id
        else:
            sector = Sector(name=sector_data["name"], description=sector_data["description"])
            db.add(sector)
            await db.flush()
            sector_map[sector.name] = sector.id

    await db.commit()
    return sector_map


async def seed_stocks(db: AsyncSession, sector_map: dict[str, int]) -> None:
    """Seed top 30 BSE stocks."""
    for stock_data in STOCKS:
        # Check if stock already exists by bse_code
        stmt = select(Stock).where(Stock.bse_code == stock_data["bse_code"])
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            continue

        sector_id = sector_map.get(stock_data["sector"])
        if sector_id is None:
            print(f"  Warning: Sector '{stock_data['sector']}' not found for {stock_data['ticker_symbol']}, skipping.")
            continue

        stock = Stock(
            bse_code=stock_data["bse_code"],
            ticker_symbol=stock_data["ticker_symbol"],
            company_name=stock_data["company_name"],
            sector_id=sector_id,
            current_price=stock_data["current_price"],
            market_cap=stock_data["market_cap"],
            is_active=True,
        )
        db.add(stock)

    await db.commit()


async def seed_education_modules(db: AsyncSession) -> None:
    """Seed education modules covering required topics."""
    for module_data in EDUCATION_MODULES:
        # Check if module with same title already exists
        stmt = select(EducationModule).where(EducationModule.title == module_data["title"])
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            continue

        module = EducationModule(
            title=module_data["title"],
            content=module_data["content"],
            difficulty=module_data["difficulty"],
            category=module_data["category"],
            sector_relevance=module_data["sector_relevance"],
            order_index=module_data["order_index"],
        )
        db.add(module)

    await db.commit()


async def main() -> None:
    """Run the seed script."""
    print("=" * 60)
    print("BSE Stock Investment Tracker — Database Seed Script")
    print("=" * 60)

    async with async_session_factory() as db:
        # 1. Seed sectors
        print("\n[1/3] Seeding BSE sectors...")
        sector_map = await seed_sectors(db)
        print(f"  ✓ {len(sector_map)} sectors ready")

        # 2. Seed stocks
        print("\n[2/3] Seeding stock data (top 30 BSE stocks)...")
        await seed_stocks(db, sector_map)
        # Count stocks in DB
        result = await db.execute(select(Stock))
        stock_count = len(result.scalars().all())
        print(f"  ✓ {stock_count} stocks in database")

        # 3. Seed education modules
        print("\n[3/3] Seeding education modules...")
        await seed_education_modules(db)
        result = await db.execute(select(EducationModule))
        module_count = len(result.scalars().all())
        print(f"  ✓ {module_count} education modules in database")

    # Clean up engine connections
    await engine.dispose()

    print("\n" + "=" * 60)
    print("Seed complete! Glossary terms are served from memory")
    print("(see app/services/education_service.py).")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
