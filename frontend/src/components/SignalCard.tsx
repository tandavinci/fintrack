import { Card, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import type { Action, Recommendation } from "@/lib/api";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";

const ACTION_STYLE: Record<
  Action,
  { text: string; ring: string; bar: string; chip: string; Icon: typeof Minus }
> = {
  BUY: {
    text: "text-buy",
    ring: "shadow-[0_0_0_1px_rgba(16,185,129,0.4),0_10px_40px_-10px_rgba(16,185,129,0.5)]",
    bar: "bg-buy",
    chip: "bg-buy/15 text-buy border-buy/30",
    Icon: ArrowUpRight,
  },
  SELL: {
    text: "text-sell",
    ring: "shadow-[0_0_0_1px_rgba(244,63,94,0.4),0_10px_40px_-10px_rgba(244,63,94,0.5)]",
    bar: "bg-sell",
    chip: "bg-sell/15 text-sell border-sell/30",
    Icon: ArrowDownRight,
  },
  HOLD: {
    text: "text-hold",
    ring: "shadow-[0_0_0_1px_rgba(245,158,11,0.4),0_10px_40px_-10px_rgba(245,158,11,0.5)]",
    bar: "bg-hold",
    chip: "bg-hold/15 text-hold border-hold/30",
    Icon: Minus,
  },
};

export function SignalCard({ rec }: { rec: Recommendation }) {
  const style = ACTION_STYLE[rec.action];
  const { Icon } = style;
  const pct = Math.round(rec.confidence * 100);

  return (
    <Card className={cn("flex flex-col gap-5", style.ring)}>
      <div className="flex items-start justify-between">
        <CardTitle>Signal</CardTitle>
        <span
          className={cn(
            "rounded-full border px-2.5 py-1 text-[11px] font-semibold",
            style.chip
          )}
        >
          {rec.horizon_days}-day horizon
        </span>
      </div>

      <div className="flex items-center gap-4">
        <div
          className={cn(
            "flex h-16 w-16 items-center justify-center rounded-2xl bg-surface-2",
            style.text
          )}
        >
          <Icon className="h-9 w-9" strokeWidth={2.5} />
        </div>
        <div>
          <div className={cn("text-4xl font-extrabold leading-none", style.text)}>
            {rec.action}
          </div>
          <div className="mt-1 text-sm text-muted">{rec.ticker}</div>
        </div>
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="text-muted">Confidence</span>
          <span className={cn("font-semibold", style.text)}>{pct}%</span>
        </div>
        <div className="h-2.5 w-full overflow-hidden rounded-full bg-surface-2">
          <div
            className={cn("h-full rounded-full transition-all", style.bar)}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <p className="text-sm leading-relaxed text-slate-300">{rec.rationale}</p>
    </Card>
  );
}
