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
