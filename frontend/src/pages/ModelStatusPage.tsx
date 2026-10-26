import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import { Card, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { fetchModelStatus, type ModelStatus } from "@/lib/api";

/**
 * Model Status — a calm read-only view of the local-LLM gateway: whether Ollama
 * is reachable, the configured model + num_ctx cap, and a summary of the
 * gateway's call log (total calls, calls by route, valid-rate, latency).
 */
export function ModelStatusPage() {
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStatus(await fetchModelStatus());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white">Model Status</h2>
          <p className="text-xs text-muted">
            Local LLM gateway — liveness &amp; call log
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex h-11 w-11 items-center justify-center rounded-xl border border-border bg-surface-2 text-muted transition-colors hover:text-white disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
        </button>
      </div>

      {error && (
        <div className="mt-5 flex items-start gap-3 rounded-xl border border-sell/30 bg-sell/10 px-4 py-3 text-sm text-sell">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <p>{error}</p>
        </div>
      )}

      {!error && loading && !status && (
        <div className="mt-5 flex flex-col items-center justify-center gap-3 rounded-xl2 py-24 text-muted">
          <Loader2 className="h-8 w-8 animate-spin text-brand" />
          <p className="text-sm">Checking the gateway…</p>
        </div>
      )}

      {!error && status && (
        <div className="mt-5 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          <LivenessCard status={status} />
          <StatCard label="Configured model" value={status.configured_model} />
          <StatCard
            label="Context cap (num_ctx)"
            value={status.num_ctx_cap.toLocaleString()}
            hint="hard-capped by the gateway"
          />
          <StatCard
            label="Total calls logged"
            value={status.summary.total_calls.toLocaleString()}
          />
          <ValidRateCard status={status} />
          <LatencyCard status={status} />
          <div className="sm:col-span-2 lg:col-span-3">
            <ByRouteCard status={status} />
          </div>
        </div>
      )}
    </div>
  );
}

function LivenessCard({ status }: { status: ModelStatus }) {
  const up = status.ollama_up;
  return (
    <Card className="flex flex-col gap-3">
      <CardTitle>Ollama</CardTitle>
      <div className="flex items-center gap-3">
        <span
          className={cn(
            "inline-block h-3 w-3 rounded-full",
            up ? "bg-buy shadow-[0_0_0_4px_rgba(16,185,129,0.2)]" : "bg-sell shadow-[0_0_0_4px_rgba(244,63,94,0.2)]"
          )}
        />
        <span className={cn("text-lg font-bold", up ? "text-buy" : "text-sell")}>
          {up ? "Reachable" : "Unreachable"}
        </span>
      </div>
      <p className="break-all text-xs text-muted">{status.ollama_base_url}</p>
    </Card>
  );
}

function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <Card className="flex flex-col gap-2">
      <CardTitle>{label}</CardTitle>
      <div className="text-xl font-bold text-white">{value || "—"}</div>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </Card>
  );
}

function ValidRateCard({ status }: { status: ModelStatus }) {
  const { valid_count, invalid_count, valid_rate } = status.summary;
  const pct = valid_rate === null ? null : Math.round(valid_rate * 1000) / 10;
  const good = pct === null || pct >= 95;
  return (
    <Card className="flex flex-col gap-2">
      <CardTitle>Valid structured output</CardTitle>
      <div
        className={cn(
          "text-xl font-bold",
          pct === null ? "text-muted" : good ? "text-buy" : "text-hold"
        )}
      >
        {pct === null ? "—" : `${pct}%`}
      </div>
      <p className="text-xs text-muted">
        {valid_count} valid · {invalid_count} invalid
      </p>
    </Card>
  );
}

function LatencyCard({ status }: { status: ModelStatus }) {
  const { avg_latency_ms, max_latency_ms } = status.summary;
  return (
    <Card className="flex flex-col gap-2">
      <CardTitle>Latency</CardTitle>
      <div className="text-xl font-bold text-white">
        {avg_latency_ms === null ? "—" : `${Math.round(avg_latency_ms)} ms`}
      </div>
      <p className="text-xs text-muted">
        avg · max {max_latency_ms === null ? "—" : `${max_latency_ms} ms`}
      </p>
    </Card>
  );
}

function ByRouteCard({ status }: { status: ModelStatus }) {
  const routes = status.summary.by_route;
  return (
    <Card className="flex flex-col gap-4">
      <CardTitle>Calls by route</CardTitle>
      {routes.length === 0 ? (
        <p className="text-sm text-muted">No calls logged yet.</p>
      ) : (
        <div className="flex flex-wrap gap-3">
          {routes.map((r) => (
            <div
              key={r.route}
              className="flex min-w-24 flex-col gap-1 rounded-xl border border-border bg-surface-2/40 px-4 py-3"
            >
              <span className="text-xs font-semibold uppercase tracking-widest text-muted">
                {r.route}
              </span>
              <span className="text-lg font-bold text-white">
                {r.count.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
