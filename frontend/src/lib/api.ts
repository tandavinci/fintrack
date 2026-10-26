/**
 * API client for the FinTrack backend.
 *
 * The one endpoint used here is the read-only quant signal endpoint
 * (GET /api/quant/signal/{ticker}), which runs the existing rule-based quant
 * engine live and returns a Recommendation plus the chart series behind it.
 * No database is required for this path.
 */

export type Action = "BUY" | "HOLD" | "SELL";

export type Regime =
  | "BULLISH_TREND"
  | "BEARISH_TREND"
  | "SIDEWAYS"
  | "HIGH_VOLATILITY"
  | "BREAKOUT";

/** Mirrors backend/app/schemas/recommendation.py (the ONE contract). */
export interface Recommendation {
  ticker: string;
  as_of: string;
  action: Action;
  confidence: number;
  horizon_days: number;
  rationale: string;
  evidence: string[];
  source: "quant" | "deep" | "advisor";
  model_id: string | null;
  prompt_version: string | null;
  snapshot_id: string;
}

/** One point on the price/indicator chart. */
export interface ChartPoint {
  date: string;
  close: number;
  sma20: number | null;
  sma50: number | null;
}

/** Best-effort benchmark context (null when the benchmark fetch failed). */
export interface BenchmarkInfo {
  index: string;
  benchmark_return_pct: number;
  excess_return_pct: number;
  rating: number;
}

/** The full response from the live quant signal endpoint. */
export interface SignalResponse {
  recommendation: Recommendation;
  regime: {
    label: Regime;
    confidence: number;
    reasoning: string;
  };
  chart: ChartPoint[];
  /** Optional benchmark comparison; null/absent when unavailable. */
  benchmark?: BenchmarkInfo | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Result of a signal fetch, flagged so the UI can show a "sample data" notice. */
export interface SignalResult {
  data: SignalResponse;
  isSample: boolean;
}

export async function fetchSignal(ticker: string): Promise<SignalResult> {
  try {
    const res = await fetch(`/api/quant/signal/${encodeURIComponent(ticker)}`);
    if (!res.ok) {
      let detail = `Request failed (${res.status})`;
      try {
        const body = await res.json();
        if (body?.detail) detail = String(body.detail);
      } catch {
        /* non-JSON error body; keep the default message */
      }
      throw new ApiError(detail, res.status);
    }
    const data = (await res.json()) as SignalResponse;
    return { data, isSample: false };
  } catch {
    // Backend/database not reachable yet (Docker down) -> preview with sample
    // data so the dashboard is still usable. The UI shows a clear banner.
    const { SAMPLE_SIGNAL } = await import("./sampleData");
    return {
      data: { ...SAMPLE_SIGNAL, recommendation: { ...SAMPLE_SIGNAL.recommendation, ticker } },
      isSample: true,
    };
  }
}

/**
 * Latest stored quant + deep recommendations for a ticker, side by side.
 * Mirrors backend FusionResponse (GET /api/fusion/{ticker}). Either source may
 * be null (deep is absent until the overnight run); conflict is true only when
 * both are present and their actions disagree. We NEVER average — both verbatim.
 */
export interface FusionResponse {
  ticker: string;
  quant: Recommendation | null;
  deep: Recommendation | null;
  conflict: boolean;
}

/**
 * Fetch the stored recommendation fusion for a ticker.
 *
 * Returns null on 404 ("no stored recommendations yet") — that is a NORMAL
 * empty state (the nightly jobs haven't populated the table), not an error.
 * Any other failure throws so the caller can surface it.
 */
export async function fetchFusion(
  ticker: string
): Promise<FusionResponse | null> {
  const res = await fetch(`/api/fusion/${encodeURIComponent(ticker)}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body; keep the default message */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as FusionResponse;
}

/** The 10 golden tickers (6 .NS + 4 .BO) from tests/golden/. */
export const GOLDEN_TICKERS = [
  "RELIANCE.NS",
  "TCS.BO",
  "HDFCBANK.NS",
  "INFY.NS",
  "ICICIBANK.NS",
  "CUMMINSIND.NS",
  "PERSISTENT.NS",
  "MPHASIS.BO",
  "YESBANK.BO",
  "IDEA.BO",
] as const;

/** The kind of a system-generated alert. */
export type AlertKind = "AI" | "RISK" | "NEWS";

/** The severity of a system-generated alert. */
export type AlertSeverity = "info" | "warning" | "critical";

/**
 * One system-generated signal alert. Mirrors backend SignalAlertOut
 * (GET /api/alerts/signals). These are AI (quant pattern), RISK (portfolio
 * volatility), and NEWS (fresh headline) insights produced by the nightly
 * generator — DISTINCT from the user-configured price/trend alerts.
 */
export interface SignalAlert {
  id: number;
  ticker: string | null;
  alert_kind: AlertKind;
  severity: AlertSeverity;
  title: string;
  detail: string;
  as_of: string;
  created_at: string | null;
}

/** Options for {@link fetchSignalAlerts}. */
export interface SignalAlertsOptions {
  kind?: AlertKind;
  ticker?: string;
  limit?: number;
}

/**
 * Fetch recent system-generated alerts.
 *
 * Returns an empty array on 404 or ANY failure (backend down, table empty) —
 * "no alerts yet" is a NORMAL empty state that must never fail the page.
 */
export async function fetchSignalAlerts(
  opts: SignalAlertsOptions = {}
): Promise<SignalAlert[]> {
  const params = new URLSearchParams();
  if (opts.kind) params.set("kind", opts.kind);
  if (opts.ticker) params.set("ticker", opts.ticker);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  try {
    const res = await fetch(`/api/alerts/signals${qs ? `?${qs}` : ""}`);
    if (!res.ok) return [];
    const body = (await res.json()) as { alerts?: SignalAlert[] };
    return body.alerts ?? [];
  } catch {
    return [];
  }
}

/* -------------------------------------------------------------------------- */
/* G5 pages: Model Status, Agent Debate (deep memory), Backtest Lab           */
/* -------------------------------------------------------------------------- */

/** One route's call count in the Model Status summary. */
export interface RouteCount {
  route: string;
  count: number;
}

/** The gateway call-log summary (mirrors backend CallSummary). */
export interface CallSummary {
  total_calls: number;
  by_route: RouteCount[];
  valid_count: number;
  invalid_count: number;
  valid_rate: number | null;
  avg_latency_ms: number | null;
  max_latency_ms: number | null;
  model_id: string | null;
}

/** The full Model Status payload (mirrors backend ModelStatusResponse). */
export interface ModelStatus {
  ollama_up: boolean;
  ollama_base_url: string;
  configured_model: string;
  num_ctx_cap: number;
  log_path: string;
  summary: CallSummary;
}

/**
 * Fetch the Model Status (gateway call-log summary + Ollama liveness).
 * Read-only. Throws ApiError on a non-ok response so the page can show it.
 */
export async function fetchModelStatus(): Promise<ModelStatus> {
  const res = await fetch("/api/model/status");
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body; keep the default message */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as ModelStatus;
}

/** One deep-memory entry (mirrors backend DeepMemoryOut). */
export interface DeepMemoryEntry {
  ticker: string;
  trade_date: string;
  rating: string | null;
  pending: boolean;
  raw_return: string | null;
  alpha_return: string | null;
  holding_days: string | null;
  resolved_date: string | null;
  decision: string;
  reflection: string;
  created_at: string | null;
}

/** The deep-memory list response (mirrors backend DeepMemoryResponse). */
export interface DeepMemoryResponse {
  ticker: string | null;
  count: number;
  entries: DeepMemoryEntry[];
}

/**
 * Fetch recent deep-memory entries (the Agent Debate viewer's data source).
 * When a ticker is given, uses the /{ticker} path variant; otherwise lists all.
 * An empty table is a NORMAL state (count: 0), not an error — so any failure
 * collapses to an empty response rather than throwing.
 */
export async function fetchDeepMemory(
  ticker?: string
): Promise<DeepMemoryResponse> {
  const empty: DeepMemoryResponse = { ticker: ticker ?? null, count: 0, entries: [] };
  try {
    const path = ticker
      ? `/api/deep/memory/${encodeURIComponent(ticker)}`
      : "/api/deep/memory";
    const res = await fetch(path);
    if (!res.ok) return empty;
    return (await res.json()) as DeepMemoryResponse;
  } catch {
    return empty;
  }
}

/** One per-prediction row in a backtest report (mirrors engine to_dict()). */
export interface BacktestResultRow {
  prediction_date: string;
  signal: string;
  predicted_change: number;
  actual_change: number;
  direction_correct: boolean;
  signal_profitable: boolean;
  regime: string;
  lesson: string;
}

/** The backtest report (mirrors the engine's BacktestReport.to_dict()). */
export interface BacktestReport {
  ticker: string;
  interval: string;
  total_predictions: number;
  direction_accuracy: number;
  signal_accuracy: number;
  signal_profitability: number;
  avg_error_pct: number;
  total_return_pct: number;
  sharpe_ratio: number;
  max_drawdown_pct: number;
  lessons_learned: string[];
  regime_performance: Record<string, unknown>;
  results: BacktestResultRow[];
}

/**
 * Run a rule-based backtest for a ticker (the Backtest Lab's data source).
 * Slow-ish (a few seconds): it pulls live prices and walks the window.
 * Throws ApiError on a non-ok response (404 = not enough data) so the page can
 * show the message.
 */
export async function fetchBacktest(ticker: string): Promise<BacktestReport> {
  const res = await fetch(`/api/backtest/${encodeURIComponent(ticker)}`);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body; keep the default message */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as BacktestReport;
}
