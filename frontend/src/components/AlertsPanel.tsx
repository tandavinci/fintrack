import { Card, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import type { AlertKind, AlertSeverity, SignalAlert } from "@/lib/api";
import { Bell, BrainCircuit, Newspaper, ShieldAlert } from "lucide-react";

/**
 * System-generated alerts panel (Step 5 / G5). Lists the recent AI / RISK /
 * NEWS insights produced by the nightly generator (read-only). This is DISTINCT
 * from the user-configured price/trend alerts. Color-coded by severity, with
 * the kind shown as a small chip. Empty/failed both collapse to a calm empty
 * state — this panel never fails the page.
 */

const SEVERITY_STYLE: Record<AlertSeverity, { border: string; text: string; dot: string }> = {
  critical: { border: "border-sell/30", text: "text-sell", dot: "bg-sell" },
  warning: { border: "border-hold/30", text: "text-hold", dot: "bg-hold" },
  info: { border: "border-border", text: "text-muted", dot: "bg-brand" },
};

const KIND_META: Record<AlertKind, { label: string; Icon: typeof Bell }> = {
  AI: { label: "AI", Icon: BrainCircuit },
  RISK: { label: "Risk", Icon: ShieldAlert },
  NEWS: { label: "News", Icon: Newspaper },
};

function AlertRow({ alert }: { alert: SignalAlert }) {
  const sev = SEVERITY_STYLE[alert.severity] ?? SEVERITY_STYLE.info;
  const kind = KIND_META[alert.alert_kind] ?? KIND_META.AI;
  const { Icon } = kind;

  return (
    <li
      className={cn(
        "flex items-start gap-3 rounded-xl border bg-surface-2/40 p-3",
        sev.border
      )}
    >
      <span className={cn("mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-2", sev.text)}>
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "rounded-full border px-2 py-0.5 text-[10px] font-semibold",
              sev.border,
              sev.text
            )}
          >
            {kind.label}
          </span>
          {alert.ticker && (
            <span className="text-[11px] font-medium text-slate-300">{alert.ticker}</span>
          )}
          <span className="ml-auto text-[10px] text-muted">{alert.as_of}</span>
        </div>
        <p className="mt-1 truncate text-sm font-medium text-slate-100" title={alert.title}>
          {alert.title}
        </p>
        {alert.detail && (
          <p className="mt-0.5 line-clamp-2 text-xs text-muted" title={alert.detail}>
            {alert.detail}
          </p>
        )}
      </div>
    </li>
  );
}

export function AlertsPanel({ alerts }: { alerts: SignalAlert[] }) {
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <CardTitle>Alerts</CardTitle>
        <span className="flex items-center gap-1 text-[11px] text-muted">
          <Bell className="h-3.5 w-3.5" />
          {alerts.length > 0 ? `${alerts.length} recent` : "system-generated"}
        </span>
      </div>

      {alerts.length === 0 ? (
        <p className="rounded-xl border border-border bg-surface-2/40 px-4 py-6 text-center text-sm text-muted">
          No alerts yet — generated after the nightly run.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {alerts.map((a) => (
            <AlertRow key={a.id} alert={a} />
          ))}
        </ul>
      )}
    </Card>
  );
}
