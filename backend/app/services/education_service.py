"""Education service for managing learning modules, progress, and glossary.

Provides methods for listing modules by difficulty, retrieving content,
tracking user progress, serving glossary terms, and contextual suggestions.
"""

from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.education import DifficultyEnum, EducationModule, EducationProgress


# Stock market glossary terms
GLOSSARY: list[dict[str, str]] = [
    {"term": "Bull Market", "definition": "A market condition where prices are rising or expected to rise."},
    {"term": "Bear Market", "definition": "A market condition where prices are falling or expected to fall."},
    {"term": "Portfolio", "definition": "A collection of financial investments like stocks, bonds, commodities, and cash equivalents."},
    {"term": "Dividend", "definition": "A portion of a company's earnings distributed to shareholders."},
    {"term": "Market Capitalization", "definition": "The total market value of a company's outstanding shares, calculated as share price multiplied by total shares."},
    {"term": "P/E Ratio", "definition": "Price-to-Earnings ratio; the ratio of a company's share price to its earnings per share."},
    {"term": "IPO", "definition": "Initial Public Offering; the first time a company offers its shares to the public on a stock exchange."},
    {"term": "Candlestick Pattern", "definition": "A chart pattern used in technical analysis showing the open, high, low, and close prices for a given period."},
    {"term": "Moving Average", "definition": "A technical indicator that smooths price data by calculating the average closing price over a specified number of periods."},
    {"term": "Support Level", "definition": "A price level where a stock tends to stop falling due to increased buying interest."},
    {"term": "Resistance Level", "definition": "A price level where a stock tends to stop rising due to increased selling pressure."},
    {"term": "Volume", "definition": "The number of shares traded during a given time period."},
    {"term": "Volatility", "definition": "A statistical measure of the dispersion of returns for a given security or market index."},
    {"term": "Blue Chip Stock", "definition": "Shares of large, well-established, and financially stable companies with a history of reliable performance."},
    {"term": "Sensex", "definition": "The BSE Sensitive Index; a benchmark index of 30 well-established companies listed on the Bombay Stock Exchange."},
    {"term": "Nifty", "definition": "The NSE Nifty 50; a benchmark index of 50 companies listed on the National Stock Exchange of India."},
    {"term": "Demat Account", "definition": "A dematerialized account that holds shares and securities in electronic form."},
    {"term": "Intraday Trading", "definition": "Buying and selling stocks within the same trading day, closing all positions before market close."},
    {"term": "Stop Loss", "definition": "An order placed to sell a security when it reaches a certain price, limiting the investor's loss."},
    {"term": "Sector", "definition": "A segment of the economy grouping companies with similar business activities (e.g., IT, Pharma, Banking)."},
    {"term": "Trend Score", "definition": "A numerical value (0-100) derived from Google Trends representing public interest in a stock or sector."},
    {"term": "Diversification", "definition": "A risk management strategy that mixes a variety of investments within a portfolio."},
    {"term": "Risk Management", "definition": "The process of identifying, assessing, and controlling threats to an organization's capital and earnings."},
    {"term": "Liquidity", "definition": "The ease with which an asset can be converted into cash without significantly affecting its market price."},
    {"term": "Broker", "definition": "An intermediary who facilitates buying and selling of securities on behalf of investors."},
]


async def get_modules_by_level(
    db: AsyncSession, difficulty_level: str
) -> list[EducationModule]:
    """List education modules filtered by difficulty level.

    Args:
        db: Async database session.
        difficulty_level: One of BEGINNER, INTERMEDIATE, ADVANCED.

    Returns:
        List of EducationModule instances matching the difficulty, ordered by order_index.
    """
    stmt = (
        select(EducationModule)
        .where(EducationModule.difficulty == difficulty_level.upper())
        .order_by(EducationModule.order_index)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_module_content(db: AsyncSession, module_id: int) -> EducationModule | None:
    """Get the full content of an education module.

    Args:
        db: Async database session.
        module_id: ID of the module to retrieve.

    Returns:
        The EducationModule instance or None if not found.
    """
    stmt = select(EducationModule).where(EducationModule.id == module_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def mark_completed(
    db: AsyncSession, user_id: int, module_id: int
) -> EducationProgress:
    """Mark an education module as completed for a user.

    If a progress record already exists, it updates it to completed.
    Otherwise, creates a new progress record.

    Args:
        db: Async database session.
        user_id: ID of the user.
        module_id: ID of the module being completed.

    Returns:
        The EducationProgress record (created or updated).
    """
    # Check for existing progress record
    stmt = select(EducationProgress).where(
        EducationProgress.user_id == user_id,
        EducationProgress.module_id == module_id,
    )
    result = await db.execute(stmt)
    progress = result.scalar_one_or_none()

    if progress is not None:
        progress.is_completed = True
        progress.completed_at = datetime.now(timezone.utc)
    else:
        progress = EducationProgress(
            user_id=user_id,
            module_id=module_id,
            is_completed=True,
            completed_at=datetime.now(timezone.utc),
        )
        db.add(progress)

    await db.commit()
    await db.refresh(progress)
    return progress


def get_glossary() -> list[dict[str, str]]:
    """Return the stock market glossary terms.

    Returns:
        List of dicts with 'term' and 'definition' keys.
    """
    return GLOSSARY


async def get_contextual_suggestions(
    db: AsyncSession, stock_sector: str
) -> list[EducationModule]:
    """Suggest educational articles matching a stock's sector.

    Returns modules whose sector_relevance matches the given sector,
    plus general-purpose modules (sector_relevance is null).
    Ordered by order_index.

    Args:
        db: Async database session.
        stock_sector: The sector of the stock being viewed.

    Returns:
        List of relevant EducationModule instances.
    """
    stmt = (
        select(EducationModule)
        .where(
            (EducationModule.sector_relevance == stock_sector)
            | (EducationModule.sector_relevance.is_(None))
        )
        .order_by(EducationModule.order_index)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_user_progress(db: AsyncSession, user_id: int) -> dict:
    """Get a user's learning progress summary.

    Returns a summary with total modules, completed count, and
    a list of completed module IDs.

    Args:
        db: Async database session.
        user_id: ID of the user.

    Returns:
        Dict with keys: total_modules, completed_count, completion_percentage,
        and completed_module_ids.
    """
    # Get total module count
    total_stmt = select(func.count(EducationModule.id))
    total_result = await db.execute(total_stmt)
    total_modules = total_result.scalar() or 0

    # Get completed modules for the user
    completed_stmt = (
        select(EducationProgress)
        .where(
            EducationProgress.user_id == user_id,
            EducationProgress.is_completed.is_(True),
        )
    )
    completed_result = await db.execute(completed_stmt)
    completed_records = list(completed_result.scalars().all())

    completed_count = len(completed_records)
    completed_module_ids = [record.module_id for record in completed_records]
    completion_percentage = (
        round((completed_count / total_modules) * 100, 1) if total_modules > 0 else 0.0
    )

    return {
        "total_modules": total_modules,
        "completed_count": completed_count,
        "completion_percentage": completion_percentage,
        "completed_module_ids": completed_module_ids,
    }
