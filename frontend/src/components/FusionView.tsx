import { Card, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import type { Action, FusionResponse, Recommendation } from "@/lib/api";
import {
  ArrowDownRight,
  ArrowUpRight,
  Minus,
  TriangleAlert,
} from "lucide-react";

/**
 * Recommendation fusion view (Step 5 / G5): the latest stored quant and deep
 * recommendations SIDE BY SIDE, with a conflict banner when their actions
 * disagree. We show BOTH verbatim and never average them (STEERING rule 4).
 * Deep may be legitimately absent until the overnight run — that column then
 * shows a muted placeholder, not an error.
 */

const ACTION_STYLE: Record<
  Action,
  { text: string; chip: string; Icon: typeof Minus }
> = {
  BUY: { text: "text-buy", chip: "bg-buy/15 text-buy border-buy/30", Icon: ArrowUpRight },
  SELL: { text: "text-sell", chip: "bg-sell/15 text-sell border-sell/30", Icon: ArrowDownRight },
  HOLD: { text: "text-hold", chip: "bg-hold/15 text-hold border-hold/30", Icon: Minus },
};

function RecColumn({
  label,
  rec,
  emptyNote,
}: {
  label: string;
  rec: Recommendation | null;
  emptyNote: string;
}) {
  if (!rec) {
    return (
      <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface-2/40 p-4">
        <div className="text-xs font-semibold uppercase tracking-widest text-muted">
          {label}
        </div>
        <p className="text-sm text-muted">{emptyNote}</p>
      </div>
    );
  }

  const style = ACTION_STYLE[rec.action];
  const { Icon } = style;
  const pct = Math.round(rec.confidence * 100);

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface-2/40 p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-widest text-muted">
          {label}
        </span>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-[10px] font-semibold",
            style.chip
          )}
        >
          {rec.horizon_days}-day
        </span>
      </div>

      <div className="flex items-center gap-3">
        <div
          className={cn(
            "flex h-11 w-11 items-center justify-center rounded-xl bg-surface-2",
            style.text
          )}
        >
          <Icon className="h-6 w-6" strokeWidth={2.5} />
        </div>
        <div className={cn("text-2xl font-extrabold leading-none", style.text)}>
          {rec.action}
        </div>
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between text-[11px]">
          <span className="text-muted">Confidence</span>
          <span className={cn("font-semibold", style.text)}>{pct}%</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
          <div
            className={cn("h-full rounded-full", style.text.replace("text-", "bg-"))}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <p className="line-clamp-4 text-xs leading-relaxed text-slate-300">
        {rec.rationale}
      </p>
      <p className="text-[10px] text-muted">as of {rec.as_of}</p>
    </div>
  );
}

export function FusionView({ fusion }: { fusion: FusionResponse }) {
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <CardTitle>Quant vs Deep</CardTitle>
        <span className="text-[11px] text-muted">{fusion.ticker}</span>
      </div>

      {fusion.conflict && (
        <div className="flex items-start gap-2 rounded-lg border border-hold/30 bg-hold/10 px-3 py-2 text-xs text-hold">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="font-semibold">
            Conflict: quant and deep disagree. Both shown as-is — not averaged.
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <RecColumn
          label="Quant"
          rec={fusion.quant}
          emptyNote="No quant recommendation yet."
        />
        <RecColumn
          label="Deep"
          rec={fusion.deep}
          emptyNote="No deep recommendation yet — runs overnight."
        />
      </div>
    </Card>
  );
}
