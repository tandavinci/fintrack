import { useCallback, useEffect, useState } from "react";
import { Loader2, MessageSquare, RefreshCw } from "lucide-react";
import { Card, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { TickerSelect } from "@/components/ui/TickerSelect";
import {
  fetchDeepMemory,
  GOLDEN_TICKERS,
  type DeepMemoryEntry,
} from "@/lib/api";

/**
 * Agent Debate viewer — a read-only window into the deep lane's memory: for a
 * ticker (or all), the DECISION and REFLECTION text of each past deep run, with
 * its rating and pending/settled status. The overnight deep run populates this;
 * an empty table is the normal starting state.
 */
export function AgentDebatePage({
  ticker,
  onTicker,
}: {
  ticker: string;
  onTicker: (t: string) => void;
}) {
  const [entries, setEntries] = useState<DeepMemoryEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (t: string) => {
    setLoading(true);
    try {
      const res = await fetchDeepMemory(t);
      setEntries(res.entries);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(ticker);
  }, [ticker, load]);

  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-white">Agent Debate</h2>
          <p className="text-xs text-muted">
            Deep-lane decisions &amp; reflections per past run
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
            onClick={() => load(ticker)}
            disabled={loading}
            className="flex h-11 w-11 items-center justify-center rounded-xl border border-border bg-surface-2 text-muted transition-colors hover:text-white disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
          </button>
        </div>
      </div>

      {loading && entries.length === 0 && (
        <div className="mt-5 flex flex-col items-center justify-center gap-3 rounded-xl2 py-24 text-muted">
          <Loader2 className="h-8 w-8 animate-spin text-brand" />
          <p className="text-sm">Loading deep-run memory…</p>
        </div>
      )}

      {!loading && entries.length === 0 && <EmptyState />}

      {entries.length > 0 && (
        <div className="mt-5 flex flex-col gap-4">
          {entries.map((e) => (
            <MemoryCard key={`${e.ticker}-${e.trade_date}`} entry={e} />
          ))}
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="mt-5 flex flex-col items-center justify-center gap-3 rounded-xl2 border border-border bg-surface-2/40 py-24 text-center text-muted">
      <MessageSquare className="h-8 w-8 text-muted" />
      <p className="max-w-md text-sm">
        No deep analyses recorded yet — the overnight deep run populates this.
      </p>
    </div>
  );
}

function RatingChip({ rating }: { rating: string | null }) {
  if (!rating) return null;
  const r = rating.toUpperCase();
  const style = r.includes("BUY")
    ? "bg-buy/15 text-buy border-buy/30"
    : r.includes("SELL")
      ? "bg-sell/15 text-sell border-sell/30"
      : "bg-hold/15 text-hold border-hold/30";
  return (
    <span className={cn("rounded-full border px-2.5 py-0.5 text-xs font-semibold", style)}>
      {rating}
    </span>
  );
}

function StatusBadge({ pending }: { pending: boolean }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2.5 py-0.5 text-[11px] font-semibold",
        pending
          ? "border-hold/30 bg-hold/10 text-hold"
          : "border-bull/30 bg-bull/10 text-bull"
      )}
    >
      {pending ? "Pending" : "Settled"}
    </span>
  );
}

function MemoryCard({ entry }: { entry: DeepMemoryEntry }) {
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-white">{entry.ticker}</span>
          <RatingChip rating={entry.rating} />
          <StatusBadge pending={entry.pending} />
        </div>
        <span className="text-xs text-muted">{entry.trade_date}</span>
      </div>

      {!entry.pending && (entry.raw_return || entry.alpha_return || entry.holding_days) && (
        <div className="flex flex-wrap gap-4 text-xs text-muted">
          {entry.raw_return && (
            <span>
              Raw: <span className="font-semibold text-slate-200">{entry.raw_return}</span>
            </span>
          )}
          {entry.alpha_return && (
            <span>
              Alpha:{" "}
              <span className="font-semibold text-slate-200">{entry.alpha_return}</span>
            </span>
          )}
          {entry.holding_days && (
            <span>
              Held:{" "}
              <span className="font-semibold text-slate-200">{entry.holding_days}</span>
            </span>
          )}
          {entry.resolved_date && (
            <span>
              Resolved:{" "}
              <span className="font-semibold text-slate-200">{entry.resolved_date}</span>
            </span>
          )}
        </div>
      )}

      <div>
        <CardTitle>Decision</CardTitle>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-200">
          {entry.decision || "—"}
        </p>
      </div>

      {entry.reflection && (
        <div>
          <CardTitle>Reflection</CardTitle>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
            {entry.reflection}
          </p>
        </div>
      )}
    </Card>
  );
}
