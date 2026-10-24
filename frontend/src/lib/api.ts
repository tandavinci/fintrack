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
