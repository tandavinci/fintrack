import { useCallback, useEffect, useState } from "react";
import { Info, Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import {
  fetchFusion,
  fetchSignal,
  fetchSignalAlerts,
  GOLDEN_TICKERS,
  type FusionResponse,
  type SignalAlert,
  type SignalResponse,
} from "@/lib/api";
import { TickerSelect } from "@/components/ui/TickerSelect";
import { SignalCard } from "@/components/SignalCard";
import { RegimeBadge } from "@/components/RegimeBadge";
import { BenchmarkCard } from "@/components/BenchmarkCard";
import { PriceChart } from "@/components/PriceChart";
import { EvidencePanel } from "@/components/EvidencePanel";
import { FusionView } from "@/components/FusionView";
import { AlertsPanel } from "@/components/AlertsPanel";

/**
 * Signals & Regime — the default view. The live rule-based quant signal plus
 * its regime, chart, benchmark, evidence, the stored quant-vs-deep fusion, and
 * the system-generated alerts. This is the original single-page dashboard body,
 * unchanged in behaviour — just lifted into its own page so the shell can
 * switch between it and the other G5 pages.
 */
export function SignalsPage({
  ticker,
  onTicker,
}: {
  ticker: string;
  onTicker: (t: string) => void;
}) {
  const [data, setData] = useState<SignalResponse | null>(null);
  const [fusion, setFusion] = useState<FusionResponse | null>(null);
  const [alerts, setAlerts] = useState<SignalAlert[]>([]);
  const [isSample, setIsSample] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (t: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchSignal(t);
      setData(res.data);
      setIsSample(res.isSample);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setData(null);
    } finally {
      setLoading(false);
    }
    // The fusion view reads the STORED recommendations table independently of
    // the live signal. It is optional decoration: null (no stored recs yet)
    // and any fetch failure both collapse to "no stored recs" rather than
    // failing the page.
    try {
      setFusion(await fetchFusion(t));
    } catch {
      setFusion(null);
    }
    // System-generated alerts (AI/RISK/NEWS) from the nightly run. Read-only,
    // optional decoration: any failure / empty collapses to the panel's own
    // empty state and never fails the page.
    try {
      setAlerts(await fetchSignalAlerts({ limit: 20 }));
    } catch {
      setAlerts([]);
    }
  }, []);

  useEffect(() => {
    load(ticker);
  }, [ticker, load]);

  return (
    <div>
      <div className="flex items-center justify-end gap-2">
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

      {error && (
        <div className="mt-5">
          <ErrorState message={error} onRetry={() => load(ticker)} />
        </div>
      )}

      {!error && loading && !data && (
        <div className="mt-5">
          <LoadingState ticker={ticker} />
        </div>
      )}

      {!error && data && isSample && (
        <div className="mt-5">
          <SampleBanner />
        </div>
      )}

      {!error && data && (
        <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <SignalCard rec={data.recommendation} />
          </div>
          <div className="lg:col-span-2">
            <PriceChart data={data.chart} />
          </div>
          <div className="lg:col-span-1">
            <RegimeBadge
              regime={data.regime.label}
              confidence={data.regime.confidence}
              reasoning={data.regime.reasoning}
            />
          </div>
          <div className="lg:col-span-1">
            <BenchmarkCard benchmark={data.benchmark} />
          </div>
          <div className="lg:col-span-1">
            <EvidencePanel evidence={data.recommendation.evidence} />
          </div>
          <div className="lg:col-span-3">
            {fusion ? <FusionView fusion={fusion} /> : <NoFusionNote />}
          </div>
          <div className="lg:col-span-3">
            <AlertsPanel alerts={alerts} />
          </div>
        </div>
      )}
    </div>
  );
}

function SampleBanner() {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-hold/30 bg-hold/10 px-4 py-3 text-sm text-hold">
      <Info className="mt-0.5 h-4 w-4 shrink-0" />
      <p>
        <span className="font-semibold">Preview mode — sample data.</span> The
        live backend isn't reachable yet (it needs Postgres + Redis via Docker).
        The dashboard below uses a fixed sample so you can see the UI. It will
        switch to live data automatically once the backend is up.
      </p>
    </div>
  );
}

function NoFusionNote() {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-border bg-surface-2/40 px-4 py-3 text-xs text-muted">
      <Info className="h-4 w-4 shrink-0" />
      <span>
        No stored recommendations yet — run the nightly jobs to populate the
        quant vs deep view.
      </span>
    </div>
  );
}

function LoadingState({ ticker }: { ticker: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl2 py-24 text-muted">
      <Loader2 className="h-8 w-8 animate-spin text-brand" />
      <p className="text-sm">
        Fetching live data and computing the signal for{" "}
        <span className="font-semibold text-slate-200">{ticker}</span>…
      </p>
      <p className="text-xs">(pulls fresh prices from Yahoo Finance)</p>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="glass flex flex-col items-center justify-center gap-3 rounded-xl2 py-20 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-sell/15 text-sell">
        <TriangleAlert className="h-6 w-6" />
      </span>
      <p className="max-w-md text-sm text-slate-300">{message}</p>
      <button
        onClick={onRetry}
        className="mt-1 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90"
      >
        Try again
      </button>
    </div>
  );
}
