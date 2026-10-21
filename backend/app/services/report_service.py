"""Report service implementing portfolio reports, capital gains reports, and CSV export.

Provides report generation operations including portfolio summary reports with profit/loss,
capital gains categorization per Indian tax rules (SHORT_TERM < 365 days, LONG_TERM >= 365 days),
and historical price data CSV export. Supports PDF (via reportlab) and CSV output formats.

Uses async SQLAlchemy sessions via dependency injection.
"""

import csv
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.portfolio import Holding, Portfolio, Transaction
from backend.app.models.stock import PriceHistory, Stock
from backend.app.schemas.report_schemas import (
    CapitalGainsEntry,
    CapitalGainsParams,
    CapitalGainsReportResponse,
    DateRange,
    GainsCategory,
    HistoryExportParams,
    HoldingReportEntry,
    PortfolioReportResponse,
    ReportFormat,
    ReportParams,
    ReportResponse,
    ReportStatus,
)


class ReportServiceError(Exception):
    """Base exception for report service errors."""

    pass


class PortfolioNotFoundError(ReportServiceError):
    """Raised when a user's portfolio cannot be found."""

    pass


class StockNotFoundError(ReportServiceError):
    """Raised when a stock cannot be found."""

    pass


class ReportService:
    """Service layer for report generation operations.

    Handles business logic for portfolio reports, capital gains reports,
    and historical data CSV export. All methods accept an AsyncSession
    for transactional control.
    """

    # Indian tax rule: holdings < 365 days are short-term
    SHORT_TERM_THRESHOLD_DAYS = 365

    async def generate_portfolio_report(
        self, session: AsyncSession, user_id: int, params: ReportParams
    ) -> PortfolioReportResponse:
        """Generate a portfolio report with all holdings, profit/loss, and current values.

        Includes each holding's purchase_date, purchase_price, current_price, and profit_loss.
        Handles up to 500 holdings within 30 seconds performance target.

        Args:
            session: Async database session.
            user_id: The user whose portfolio report to generate.
            params: Report parameters including format and optional date range.

        Returns:
            PortfolioReportResponse with all holding entries and aggregate metrics.

        Raises:
            PortfolioNotFoundError: If the user has no portfolio.
        """
        portfolio = await self._get_user_portfolio(session, user_id)
        holdings = await self._get_holdings_with_stocks(session, portfolio.id)

        report_entries: list[HoldingReportEntry] = []
        total_invested = Decimal("0.00")
        current_value = Decimal("0.00")

        for holding in holdings:
            stock = holding.stock
            if stock is None or holding.total_quantity <= 0:
                continue

            # Get earliest BUY transaction to determine purchase_date
            purchase_date = await self._get_earliest_purchase_date(session, holding.id)

            # Apply date range filter if specified
            if params.date_range:
                if purchase_date and purchase_date > params.date_range.end_date:
                    continue
                if purchase_date and purchase_date < params.date_range.start_date:
                    # Include holding but note it was purchased before range
                    pass

            purchase_price = holding.average_price
            current_price = stock.current_price or Decimal("0.00")
            quantity = holding.total_quantity

            invested = purchase_price * quantity
            value = current_price * quantity
            profit_loss = value - invested
            pct_return = (
                float((profit_loss / invested) * 100) if invested > 0 else 0.0
            )

            total_invested += invested
            current_value += value

            report_entries.append(
                HoldingReportEntry(
                    stock_id=stock.id,
                    stock_name=stock.company_name,
                    ticker_symbol=stock.ticker_symbol,
                    purchase_date=purchase_date or date.today(),
                    purchase_price=purchase_price,
                    quantity=quantity,
                    current_price=current_price,
                    profit_loss=profit_loss,
                    pct_return=pct_return,
                )
            )

        total_profit_loss = current_value - total_invested
        overall_pct_return = (
            float((total_profit_loss / total_invested) * 100)
            if total_invested > 0
            else 0.0
        )

        report_id = str(uuid.uuid4())

        return PortfolioReportResponse(
            report_id=report_id,
            holdings=report_entries,
            total_invested=total_invested,
            current_value=current_value,
            total_profit_loss=total_profit_loss,
            pct_return=overall_pct_return,
            generated_at=datetime.now(timezone.utc),
        )

    async def generate_capital_gains_report(
        self, session: AsyncSession, user_id: int, params: CapitalGainsParams
    ) -> CapitalGainsReportResponse:
        """Generate a capital gains report categorizing holdings as SHORT_TERM or LONG_TERM.

        Per Indian tax rules:
        - SHORT_TERM: holding period < 365 days
        - LONG_TERM: holding period >= 365 days

        The holding period is calculated from the purchase_date to the report end_date.

        Args:
            session: Async database session.
            user_id: The user whose capital gains to report.
            params: Capital gains parameters including format and date range.

        Returns:
            CapitalGainsReportResponse with categorized entries and aggregate gains.

        Raises:
            PortfolioNotFoundError: If the user has no portfolio.
        """
        portfolio = await self._get_user_portfolio(session, user_id)
        holdings = await self._get_holdings_with_stocks(session, portfolio.id)

        entries: list[CapitalGainsEntry] = []
        short_term_gains = Decimal("0.00")
        long_term_gains = Decimal("0.00")

        report_date = params.date_range.end_date

        for holding in holdings:
            stock = holding.stock
            if stock is None or holding.total_quantity <= 0:
                continue

            purchase_date = await self._get_earliest_purchase_date(session, holding.id)
            if purchase_date is None:
                purchase_date = date.today()

            # Filter: only include holdings purchased within or before the date range
            if purchase_date > params.date_range.end_date:
                continue

            holding_days = (report_date - purchase_date).days
            category = (
                GainsCategory.SHORT_TERM
                if holding_days < self.SHORT_TERM_THRESHOLD_DAYS
                else GainsCategory.LONG_TERM
            )

            purchase_price = holding.average_price
            current_price = stock.current_price or Decimal("0.00")
            quantity = holding.total_quantity
            profit_loss = (current_price - purchase_price) * quantity

            if category == GainsCategory.SHORT_TERM:
                short_term_gains += profit_loss
            else:
                long_term_gains += profit_loss

            entries.append(
                CapitalGainsEntry(
                    stock_id=stock.id,
                    stock_name=stock.company_name,
                    ticker_symbol=stock.ticker_symbol,
                    purchase_date=purchase_date,
                    purchase_price=purchase_price,
                    quantity=quantity,
                    current_price=current_price,
                    profit_loss=profit_loss,
                    category=category,
                    holding_days=holding_days,
                )
            )

        total_gains = short_term_gains + long_term_gains
        report_id = str(uuid.uuid4())

        return CapitalGainsReportResponse(
            report_id=report_id,
            entries=entries,
            short_term_gains=short_term_gains,
            long_term_gains=long_term_gains,
            total_gains=total_gains,
            date_range=params.date_range,
            generated_at=datetime.now(timezone.utc),
        )

    async def export_history_csv(
        self, session: AsyncSession, params: HistoryExportParams
    ) -> bytes:
        """Export price history for a stock within a date range as CSV.

        Returns raw CSV bytes suitable for file download. Includes columns:
        date, open_price, high_price, low_price, close_price, volume.

        Args:
            session: Async database session.
            params: Export parameters including stock_id, start_date, end_date.

        Returns:
            CSV content as bytes.

        Raises:
            StockNotFoundError: If the stock_id does not reference a valid stock.
        """
        # Validate stock exists
        stock = await self._get_stock(session, params.stock_id)

        # Query price history within date range
        stmt = (
            select(PriceHistory)
            .where(
                PriceHistory.stock_id == params.stock_id,
                PriceHistory.date >= params.start_date,
                PriceHistory.date <= params.end_date,
            )
            .order_by(PriceHistory.date.asc())
        )
        result = await session.execute(stmt)
        records = result.scalars().all()

        # Generate CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["date", "open_price", "high_price", "low_price", "close_price", "volume"])

        for record in records:
            writer.writerow([
                record.date.isoformat(),
                str(record.open_price),
                str(record.high_price),
                str(record.low_price),
                str(record.close_price),
                str(record.volume),
            ])

        csv_bytes = output.getvalue().encode("utf-8")
        output.close()
        return csv_bytes

    async def generate_portfolio_report_pdf(
        self, session: AsyncSession, user_id: int, params: ReportParams
    ) -> bytes:
        """Generate a portfolio report as PDF bytes using reportlab.

        Args:
            session: Async database session.
            user_id: The user whose portfolio report to generate.
            params: Report parameters.

        Returns:
            PDF content as bytes.

        Raises:
            PortfolioNotFoundError: If the user has no portfolio.
        """
        report = await self.generate_portfolio_report(session, user_id, params)

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        elements: list = []

        # Title
        title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Heading1"],
            fontSize=16,
            spaceAfter=12,
        )
        elements.append(Paragraph("Portfolio Report", title_style))
        elements.append(Spacer(1, 0.5 * cm))

        # Summary
        summary_text = (
            f"Total Invested: ₹{report.total_invested:,.2f} | "
            f"Current Value: ₹{report.current_value:,.2f} | "
            f"P/L: ₹{report.total_profit_loss:,.2f} ({report.pct_return:.2f}%)"
        )
        elements.append(Paragraph(summary_text, styles["Normal"]))
        elements.append(Spacer(1, 0.5 * cm))

        # Table header
        table_data = [
            ["Stock", "Ticker", "Qty", "Purchased", "Avg Price", "Current", "P/L"]
        ]

        for entry in report.holdings:
            table_data.append([
                entry.stock_name[:20],
                entry.ticker_symbol,
                str(entry.quantity),
                entry.purchase_date.isoformat(),
                f"₹{entry.purchase_price:,.2f}",
                f"₹{entry.current_price:,.2f}",
                f"₹{entry.profit_loss:,.2f}",
            ])

        if len(table_data) > 1:
            table = Table(table_data, repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.beige, colors.white]),
            ]))
            elements.append(table)

        # Generated timestamp
        elements.append(Spacer(1, 1 * cm))
        elements.append(
            Paragraph(
                f"Generated: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                styles["Normal"],
            )
        )

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes

    async def generate_capital_gains_report_pdf(
        self, session: AsyncSession, user_id: int, params: CapitalGainsParams
    ) -> bytes:
        """Generate a capital gains report as PDF bytes using reportlab.

        Args:
            session: Async database session.
            user_id: The user whose capital gains report to generate.
            params: Capital gains parameters.

        Returns:
            PDF content as bytes.

        Raises:
            PortfolioNotFoundError: If the user has no portfolio.
        """
        report = await self.generate_capital_gains_report(session, user_id, params)

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        elements: list = []

        # Title
        title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Heading1"],
            fontSize=16,
            spaceAfter=12,
        )
        elements.append(Paragraph("Capital Gains Report", title_style))
        elements.append(Spacer(1, 0.5 * cm))

        # Date range
        elements.append(
            Paragraph(
                f"Period: {report.date_range.start_date} to {report.date_range.end_date}",
                styles["Normal"],
            )
        )
        elements.append(Spacer(1, 0.3 * cm))

        # Summary
        summary_text = (
            f"Short-term Gains: ₹{report.short_term_gains:,.2f} | "
            f"Long-term Gains: ₹{report.long_term_gains:,.2f} | "
            f"Total Gains: ₹{report.total_gains:,.2f}"
        )
        elements.append(Paragraph(summary_text, styles["Normal"]))
        elements.append(Spacer(1, 0.5 * cm))

        # Table
        table_data = [
            ["Stock", "Ticker", "Qty", "Purchased", "Days", "Category", "P/L"]
        ]

        for entry in report.entries:
            table_data.append([
                entry.stock_name[:20],
                entry.ticker_symbol,
                str(entry.quantity),
                entry.purchase_date.isoformat(),
                str(entry.holding_days),
                entry.category.value,
                f"₹{entry.profit_loss:,.2f}",
            ])

        if len(table_data) > 1:
            table = Table(table_data, repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.beige, colors.white]),
            ]))
            elements.append(table)

        # Generated timestamp
        elements.append(Spacer(1, 1 * cm))
        elements.append(
            Paragraph(
                f"Generated: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                styles["Normal"],
            )
        )

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes

    async def generate_portfolio_report_csv(
        self, session: AsyncSession, user_id: int, params: ReportParams
    ) -> bytes:
        """Generate a portfolio report as CSV bytes.

        Args:
            session: Async database session.
            user_id: The user whose portfolio report to generate.
            params: Report parameters.

        Returns:
            CSV content as bytes.
        """
        report = await self.generate_portfolio_report(session, user_id, params)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "stock_id", "stock_name", "ticker_symbol", "purchase_date",
            "purchase_price", "quantity", "current_price", "profit_loss", "pct_return"
        ])

        for entry in report.holdings:
            writer.writerow([
                entry.stock_id,
                entry.stock_name,
                entry.ticker_symbol,
                entry.purchase_date.isoformat(),
                str(entry.purchase_price),
                entry.quantity,
                str(entry.current_price),
                str(entry.profit_loss),
                f"{entry.pct_return:.2f}",
            ])

        # Add summary row
        writer.writerow([])
        writer.writerow(["Summary"])
        writer.writerow(["Total Invested", str(report.total_invested)])
        writer.writerow(["Current Value", str(report.current_value)])
        writer.writerow(["Total P/L", str(report.total_profit_loss)])
        writer.writerow(["Return %", f"{report.pct_return:.2f}"])

        csv_bytes = output.getvalue().encode("utf-8")
        output.close()
        return csv_bytes

    async def generate_capital_gains_report_csv(
        self, session: AsyncSession, user_id: int, params: CapitalGainsParams
    ) -> bytes:
        """Generate a capital gains report as CSV bytes.

        Args:
            session: Async database session.
            user_id: The user whose capital gains report to generate.
            params: Capital gains parameters.

        Returns:
            CSV content as bytes.
        """
        report = await self.generate_capital_gains_report(session, user_id, params)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "stock_id", "stock_name", "ticker_symbol", "purchase_date",
            "purchase_price", "quantity", "current_price", "profit_loss",
            "category", "holding_days"
        ])

        for entry in report.entries:
            writer.writerow([
                entry.stock_id,
                entry.stock_name,
                entry.ticker_symbol,
                entry.purchase_date.isoformat(),
                str(entry.purchase_price),
                entry.quantity,
                str(entry.current_price),
                str(entry.profit_loss),
                entry.category.value,
                entry.holding_days,
            ])

        # Add summary
        writer.writerow([])
        writer.writerow(["Summary"])
        writer.writerow(["Short-term Gains", str(report.short_term_gains)])
        writer.writerow(["Long-term Gains", str(report.long_term_gains)])
        writer.writerow(["Total Gains", str(report.total_gains)])
        writer.writerow(["Period", f"{report.date_range.start_date} to {report.date_range.end_date}"])

        csv_bytes = output.getvalue().encode("utf-8")
        output.close()
        return csv_bytes

    async def get_report_status(self, report_id: str) -> ReportResponse:
        """Get the status of a report generation request.

        For synchronous report generation (current implementation), reports
        are always COMPLETED immediately. This method supports future async
        background report generation via Celery.

        Args:
            report_id: The report UUID.

        Returns:
            ReportResponse with status information.
        """
        return ReportResponse(
            report_id=report_id,
            status=ReportStatus.COMPLETED,
            format=ReportFormat.PDF,
            created_at=datetime.now(timezone.utc),
            download_url=f"/api/v1/reports/{report_id}/download",
        )

    # --- Internal helpers ---

    async def _get_user_portfolio(
        self, session: AsyncSession, user_id: int
    ) -> Portfolio:
        """Get the user's portfolio, raising an error if not found.

        Args:
            session: Async database session.
            user_id: The user's ID.

        Returns:
            The Portfolio instance.

        Raises:
            PortfolioNotFoundError: If no portfolio exists for this user.
        """
        stmt = select(Portfolio).where(Portfolio.user_id == user_id)
        result = await session.execute(stmt)
        portfolio = result.scalars().first()

        if portfolio is None:
            raise PortfolioNotFoundError(
                f"No portfolio found for user {user_id}"
            )

        return portfolio

    async def _get_holdings_with_stocks(
        self, session: AsyncSession, portfolio_id: int
    ) -> list[Holding]:
        """Get all holdings for a portfolio with their associated stock data loaded.

        Args:
            session: Async database session.
            portfolio_id: The portfolio's ID.

        Returns:
            List of Holding instances with stock relationships loaded.
        """
        stmt = (
            select(Holding)
            .options(selectinload(Holding.stock))
            .where(Holding.portfolio_id == portfolio_id)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _get_earliest_purchase_date(
        self, session: AsyncSession, holding_id: int
    ) -> date | None:
        """Get the earliest BUY transaction date for a holding.

        Args:
            session: Async database session.
            holding_id: The holding's ID.

        Returns:
            The earliest purchase date, or None if no transactions exist.
        """
        stmt = (
            select(Transaction.transaction_date)
            .where(
                Transaction.holding_id == holding_id,
                Transaction.transaction_type == "BUY",
            )
            .order_by(Transaction.transaction_date.asc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_stock(self, session: AsyncSession, stock_id: int) -> Stock:
        """Get a stock by ID, raising an error if not found.

        Args:
            session: Async database session.
            stock_id: The stock's ID.

        Returns:
            The Stock instance.

        Raises:
            StockNotFoundError: If the stock does not exist.
        """
        stmt = select(Stock).where(Stock.id == stock_id)
        result = await session.execute(stmt)
        stock = result.scalars().first()

        if stock is None:
            raise StockNotFoundError(f"Stock with id {stock_id} not found")

        return stock
