"""Pydantic schemas package.

Home of the ONE shared Recommendation contract (decision 0006) AND the
Tracker's product schemas. The contract symbols are re-exported here so the
single import home holds; the Tracker schemas are re-exported unchanged.
"""

# --- Shared contract (single source of truth; never fork) ---
from backend.app.schemas.recommendation import Action, Recommendation
from backend.app.schemas.snapshot import SNAPSHOT_TAIL_N, compute_snapshot_id
from backend.app.schemas.fusion import fuse

# --- Tracker product schemas (relocated verbatim from SRC-T) ---
from backend.app.schemas.alert_schemas import (
    AlertCreate,
    AlertListResponse,
    AlertResponse,
    AlertType,
    AlertUpdate,
    TriggeredAlert,
)
from backend.app.schemas.report_schemas import (
    CapitalGainsEntry,
    CapitalGainsParams,
    CapitalGainsReportResponse,
    DateRange,
    DifficultyLevel,
    EducationModuleListResponse,
    EducationModuleResponse,
    EducationProgressResponse,
    GainsCategory,
    GlossaryTerm,
    HistoryExportParams,
    HoldingReportEntry,
    InsightCompareRequest,
    InsightCompareResponse,
    InsightScoreResponse,
    InsightSignal,
    PortfolioReportResponse,
    ReportFormat,
    ReportParams,
    ReportResponse,
    ReportStatus,
    TopOpportunityResponse,
    UserEducationSummary,
)

__all__ = [
    # --- contract (prepended) ---
    "Action",
    "Recommendation",
    "SNAPSHOT_TAIL_N",
    "compute_snapshot_id",
    "fuse",
    # Alert schemas
    "AlertCreate",
    "AlertListResponse",
    "AlertResponse",
    "AlertType",
    "AlertUpdate",
    "TriggeredAlert",
    # Insight schemas
    "InsightCompareRequest",
    "InsightCompareResponse",
    "InsightScoreResponse",
    "InsightSignal",
    "TopOpportunityResponse",
    # Education schemas
    "DifficultyLevel",
    "EducationModuleListResponse",
    "EducationModuleResponse",
    "EducationProgressResponse",
    "GlossaryTerm",
    "UserEducationSummary",
    # Report schemas
    "CapitalGainsEntry",
    "CapitalGainsParams",
    "CapitalGainsReportResponse",
    "DateRange",
    "GainsCategory",
    "HistoryExportParams",
    "HoldingReportEntry",
    "PortfolioReportResponse",
    "ReportFormat",
    "ReportParams",
    "ReportResponse",
    "ReportStatus",
]
