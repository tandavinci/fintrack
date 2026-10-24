/**
 * Sample data for previewing the UI without a running backend/database.
 *
 * TK's G2 work routed the quant data path through Postgres + Redis, so the live
 * endpoint needs Docker up. Until then, the app falls back to this sample so the
 * dashboard is fully viewable. The shape is identical to the real SignalResponse,
 * so when the backend is reachable the UI switches to live data with no changes.
 *
 * The numbers mirror a real RELIANCE.NS run captured on 2026-10-22.
 */
import type { Regime, SignalResponse } from "./api";

function buildChart() {
  // A deterministic, realistic-looking daily series (120 points) so the chart
  // looks alive in the preview. Not real market data — clearly a sample.
  const points: { date: string; close: number; sma20: number; sma50: number }[] =
    [];
  let close = 1180;
  const start = new Date("2026-05-01");
  const closes: number[] = [];
  for (let i = 0; i < 120; i++) {
    // Gentle drift + a mild dip in the middle to show regime character.
    const wobble = Math.sin(i / 7) * 14 + Math.cos(i / 13) * 9;
    const drift = i < 70 ? i * 0.9 : (70 - (i - 70) * 1.3) * 0.9;
    close = 1180 + drift + wobble;
    closes.push(close);
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const sma20 =
      i >= 19
        ? closes.slice(i - 19, i + 1).reduce((a, b) => a + b, 0) / 20
        : NaN;
    const sma50 =
      i >= 49
        ? closes.slice(i - 49, i + 1).reduce((a, b) => a + b, 0) / 50
        : NaN;
    points.push({
      date: d.toISOString().slice(0, 10),
      close: Math.round(close * 100) / 100,
      sma20: Number.isNaN(sma20) ? (NaN as unknown as number) : Math.round(sma20 * 100) / 100,
      sma50: Number.isNaN(sma50) ? (NaN as unknown as number) : Math.round(sma50 * 100) / 100,
    });
  }
  return points.map((p) => ({
    date: p.date,
    close: p.close,
    sma20: Number.isNaN(p.sma20) ? null : p.sma20,
    sma50: Number.isNaN(p.sma50) ? null : p.sma50,
  }));
}

export const SAMPLE_SIGNAL: SignalResponse = {
  recommendation: {
    ticker: "RELIANCE.NS",
    as_of: "2026-10-22",
    action: "HOLD",
    confidence: 0.41,
    horizon_days: 10,
    rationale:
      "Regime (BEARISH_TREND): bearish trend favors selling | MA Analysis: price between MAs, mixed signal | RSI (46.6): neutral RSI zone | MACD: MACD above signal, bullish | Volume: normal volume, no strong signal | Bollinger: price in middle of bands",
    evidence: [
      "price.current=1202.0000",
      "price.change_pct=-0.7600",
      "moving_averages.SMA_20=1200.2000",
      "moving_averages.SMA_50=1258.0500",
      "momentum.RSI=46.5800",
      "momentum.MACD=-17.8550",
      "momentum.MACD_Signal=-23.2642",
      "volatility.ATR=25.2900",
      "volatility.BB_Width=0.0921",
      "volume.Volume_Ratio=1.0900",
    ],
    source: "quant",
    model_id: null,
    prompt_version: null,
    snapshot_id: "sample0000000000",
  },
  regime: {
    label: "BEARISH_TREND" as Regime,
    confidence: 0.52,
    reasoning:
      "Market classified as BEARISH_TREND (confidence: 52%). Key factors: net price change -3.1%, SMA20 below SMA50 (death cross), RSI at 47 (bearish momentum).",
  },
  benchmark: {
    index: "^NSEI",
    benchmark_return_pct: 4.2,
    excess_return_pct: -1.8,
    rating: 3,
  },
  chart: buildChart(),
};
