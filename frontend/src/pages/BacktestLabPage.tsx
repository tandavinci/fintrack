import { useState } from "react";
import { FlaskConical, Loader2, Play, TriangleAlert } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { TickerSelect } from "@/components/ui/TickerSelect";
import {
  fetchBacktest,
  GOLDEN_TICKERS,
  type BacktestReport,
} from "@/lib/api";

/**
 * Backtest Lab — a read-only view that runs the deterministic rule-based
 * backtest over a recent window for a ticker on demand (it's slow-ish, so a
 * spinner), and renders the headline metrics as stat cards plus a small chart
 * of predicted vs actual change per prediction.
 */
export function BacktestLabPage({
  ticker,
  onTicker,
}: {
  ticker: string;
  onTicker: (t: string) => void;
}) {
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await fetchBacktest(ticker));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setReport(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-white">Backtest Lab</h2>
          <p className="text-xs text-muted">
            Deterministic rule-based backtest over a recent window
          </p>
        </div>
        <div className="flex items-center gap-2">
          <TickerSelect
            value={ticker}
            options={GOLDEN_TICKERS}
            onChange={onTicker}
            disabled={loading}
          />
          <button
            onClick={run}
            disabled={loading}
            className="flex h-11 items-center gap-2 rounded-xl bg-brand px-4 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {loading ? "Running…" : "Run backtest"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-5 flex items-start gap-3 rounded-xl border border-sell/30 bg-sell/10 px-4 py-3 text-sm text-sell">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <p>{error}</p>
        </div>
      )}

      {loading && !report && (
        <div className="mt-5 flex flex-col items-center justify-center gap-3 rounded-xl2 py-24 text-muted">
          <Loader2 className="h-8 w-8 animate-spin text-brand" />
          <p className="text-sm">
            Walking the window for{" "}
            <span className="font-semibold text-slate-200">{ticker}</span>…
          </p>
          <p className="text-xs">(pulls fresh prices — this takes a few seconds)</p>
        </div>
      )}

      {!loading && !report && !error && <InstructionalState />}

      {report && <Results report={report} />}
    </div>
  );
}

function InstructionalState() {
  return (
    <div className="mt-5 flex flex-col items-center justify-center gap-3 rounded-xl2 border border-border bg-surface-2/40 py-24 text-center text-muted">
      <FlaskConical className="h-8 w-8 text-muted" />
      <p className="max-w-md text-sm">
        Pick a ticker and run a backtest. The deterministic rule-based engine
        walks a recent daily window and reports how its signals would have done.
      </p>
    </div>
  );
}

function StatCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "good" | "bad";
}) {
  const toneClass =
    tone === "good" ? "text-buy" : tone === "bad" ? "text-sell" : "text-white";
  return (
    <Card className="flex flex-col gap-2">
      <CardTitle>{label}</CardTitle>
      <div className={cn("text-2xl font-extrabold", toneClass)}>{value}</div>
    </Card>
  );
}

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function Results({ report }: { report: BacktestReport }) {
  const chartData = report.results.map((r, i) => ({
    i: i + 1,
    date: r.prediction_date,
    predicted: r.predicted_change,
    actual: r.actual_change,
  }));

  return (
    <div className="mt-5 flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard
          label="Direction accuracy"
          value={pct(report.direction_accuracy)}
          tone={report.direction_accuracy >= 0.5 ? "good" : "bad"}
        />
        <StatCard
          label="Signal accuracy"
          value={pct(report.signal_accuracy)}
          tone={report.signal_accuracy >= 0.5 ? "good" : "bad"}
        />
        <StatCard
          label="Total return"
          value={`${report.total_return_pct >= 0 ? "+" : ""}${report.total_return_pct.toFixed(2)}%`}
          tone={report.total_return_pct >= 0 ? "good" : "bad"}
        />
        <StatCard label="Sharpe" value={report.sharpe_ratio.toFixed(2)} />
        <StatCard
          label="Max drawdown"
          value={`-${report.max_drawdown_pct.toFixed(2)}%`}
          tone="bad"
        />
      </div>

      <p className="text-xs text-muted">
        {report.total_predictions} predictions · {report.ticker} ·{" "}
        {report.interval} interval
      </p>

      {chartData.length > 0 && (
        <Card className="flex flex-col gap-4">
          <CardTitle>Predicted vs actual change (%)</CardTitle>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={chartData}
                margin={{ top: 8, right: 8, left: -8, bottom: 0 }}
              >
                <CartesianGrid
                  stroke="#263150"
                  strokeDasharray="3 3"
                  vertical={false}
                />
                <XAxis
                  dataKey="i"
                  tick={{ fill: "#8b97b8", fontSize: 11 }}
                  axisLine={{ stroke: "#263150" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: "#8b97b8", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={44}
                />
                <Tooltip
                  contentStyle={{
                    background: "#121829",
                    border: "1px solid #263150",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: "#8b97b8" }}
                />
                <Line
                  type="monotone"
                  dataKey="predicted"
                  name="Predicted"
                  stroke="#6366f1"
                  strokeWidth={1.5}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="actual"
                  name="Actual"
                  stroke="#22d3ee"
                  strokeWidth={1.5}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}
    </div>
  );
}
