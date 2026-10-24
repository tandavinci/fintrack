import { Card, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import type { BenchmarkInfo } from "@/lib/api";
import { Star } from "lucide-react";

/** Human labels for the benchmark index symbols the backend may send. */
const INDEX_LABELS: Record<string, string> = {
  "^NSEI": "Nifty 50",
  "^BSESN": "Sensex",
};

function indexLabel(symbol: string): string {
  return INDEX_LABELS[symbol] ?? symbol;
}

interface BenchmarkCardProps {
  benchmark: BenchmarkInfo | null | undefined;
}

/**
 * Benchmark context card. IMPORTANT (honest labelling): the figure here is the
 * STOCK's own buy-and-hold return over the recent window minus the index's
 * buy-and-hold — i.e. "did holding this stock beat holding the index". It is
 * NOT the strategy/signal's realised return (that proper strategy-vs-benchmark
 * score belongs to the Backtest Lab, a later step). Shown as a 1..5 rating of
 * stars, the index name, and the relative return (green positive / red
 * negative). Renders a muted "unavailable" line when the backend could not
 * compute it (benchmark === null).
 */
export function BenchmarkCard({ benchmark }: BenchmarkCardProps) {
  if (!benchmark) {
    return (
      <Card className="flex flex-col gap-3">
        <CardTitle>Stock vs Index</CardTitle>
        <p className="text-sm text-muted">Benchmark comparison unavailable.</p>
      </Card>
    );
  }

  const excess = benchmark.excess_return_pct;
  const positive = excess >= 0;
  const rating = Math.max(0, Math.min(5, Math.round(benchmark.rating)));

  return (
    <Card className="flex flex-col gap-4">
      <CardTitle>Stock vs Index</CardTitle>

      <div className="flex items-center justify-between">
        <div className="text-sm text-muted">
          buy &amp; hold vs{" "}
          <span className="font-semibold text-slate-200">
            {indexLabel(benchmark.index)}
          </span>
        </div>
        <div
          className="flex items-center gap-1"
          aria-label={`Rating ${rating} out of 5`}
          title={`Rating ${rating} / 5`}
        >
          {Array.from({ length: 5 }).map((_, i) => (
            <Star
              key={i}
              className={cn(
                "h-4 w-4",
                i < rating ? "fill-brand text-brand" : "text-border"
              )}
            />
          ))}
        </div>
      </div>

      <div className="flex items-baseline gap-2">
        <span
          className={cn(
            "text-2xl font-bold leading-none",
            positive ? "text-buy" : "text-sell"
          )}
        >
          {positive ? "+" : ""}
          {excess.toFixed(2)}%
        </span>
        <span className="text-xs text-muted">vs index (buy &amp; hold)</span>
      </div>

      <p className="text-xs text-muted">
        {indexLabel(benchmark.index)} returned{" "}
        <span className="font-medium text-slate-300">
          {benchmark.benchmark_return_pct >= 0 ? "+" : ""}
          {benchmark.benchmark_return_pct.toFixed(2)}%
        </span>{" "}
        over the same recent window. Not the signal's own return.
      </p>
    </Card>
  );
}
