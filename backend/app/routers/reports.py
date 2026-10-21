"""Reports router for portfolio reports, capital gains reports, and CSV export.

Provides endpoints for generating portfolio reports with profit/loss,
capital gains reports categorized by Indian tax rules, historical data CSV export,
and report download. All endpoints require JWT authentication.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_current_user, get_db
from backend.app.schemas.report_schemas import (
    CapitalGainsParams,
    CapitalGainsReportResponse,
    HistoryExportParams,
    PortfolioReportResponse,
    ReportFormat,
    ReportParams,
    ReportResponse,
)
from backend.app.services.report_service import (
    PortfolioNotFoundError,
    ReportService,
    ReportServiceError,
    StockNotFoundError,
)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

# Service instance shared across endpoints
_report_service = ReportService()


def get_report_service() -> ReportService:
    """Dependency to provide the ReportService instance."""
    return _report_service


@router.post("/portfolio", response_model=PortfolioReportResponse)
async def generate_portfolio_report(
    params: ReportParams,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> PortfolioReportResponse | Response:
    """Generate a portfolio report with all holdings, profit/loss, and current values.

    Supports PDF and CSV output formats. When format is PDF or CSV, returns the file
    directly with appropriate content-type headers. When format is PDF with JSON response
    model, returns the structured report data.
    """
    try:
        if params.format == ReportFormat.PDF:
            pdf_bytes = await service.generate_portfolio_report_pdf(
                db, current_user["id"], params
            )
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": "attachment; filename=portfolio_report.pdf"
                },
            )
        elif params.format == ReportFormat.CSV:
            csv_bytes = await service.generate_portfolio_report_csv(
                db, current_user["id"], params
            )
            return Response(
                content=csv_bytes,
                media_type="text/csv",
                headers={
                    "Content-Disposition": "attachment; filename=portfolio_report.csv"
                },
            )
        # Default: return JSON response model
        return await service.generate_portfolio_report(
            db, current_user["id"], params
        )
    except PortfolioNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ReportServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/capital-gains", response_model=CapitalGainsReportResponse)
async def generate_capital_gains_report(
    params: CapitalGainsParams,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> CapitalGainsReportResponse | Response:
    """Generate a capital gains report categorizing holdings as SHORT_TERM or LONG_TERM.

    Per Indian tax rules:
    - SHORT_TERM: holding period < 365 days
    - LONG_TERM: holding period >= 365 days

    Supports PDF and CSV output formats. When format is PDF or CSV, returns the file
    directly with appropriate content-type headers.
    """
    try:
        if params.format == ReportFormat.PDF:
            pdf_bytes = await service.generate_capital_gains_report_pdf(
                db, current_user["id"], params
            )
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": "attachment; filename=capital_gains_report.pdf"
                },
            )
        elif params.format == ReportFormat.CSV:
            csv_bytes = await service.generate_capital_gains_report_csv(
                db, current_user["id"], params
            )
            return Response(
                content=csv_bytes,
                media_type="text/csv",
                headers={
                    "Content-Disposition": "attachment; filename=capital_gains_report.csv"
                },
            )
        # Default: return JSON response model
        return await service.generate_capital_gains_report(
            db, current_user["id"], params
        )
    except PortfolioNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ReportServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/export/history")
async def export_history_csv(
    params: HistoryExportParams,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> Response:
    """Export historical price data for a stock as CSV.

    Returns a CSV file with columns: date, open_price, high_price, low_price,
    close_price, volume. Filtered by the specified date range.
    """
    try:
        csv_bytes = await service.export_history_csv(db, params)
        return Response(
            content=csv_bytes,
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=history_stock_{params.stock_id}"
                    f"_{params.start_date}_{params.end_date}.csv"
                )
            },
        )
    except StockNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ReportServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.get("/{report_id}/download")
async def download_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
) -> ReportResponse:
    """Download a previously generated report by its ID.

    This is a placeholder endpoint for future async report generation support.
    Currently returns report metadata with status and download URL.
    When background report generation (via Celery) is implemented, this endpoint
    will serve the actual file bytes.
    """
    report_status = await service.get_report_status(report_id)
    return report_status
