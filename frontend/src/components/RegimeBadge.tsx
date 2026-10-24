import { Card, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import type { Regime } from "@/lib/api";
import {
  Activity,
  TrendingDown,
  TrendingUp,
  Waves,
  Zap,
} from "lucide-react";

const REGIME_STYLE: Record<
  Regime,
  { label: string; text: string; chip: string; Icon: typeof Activity }
> = {
  BULLISH_TREND: {
    label: "Bullish Trend",
    text: "text-buy",
    chip: "bg-buy/15 border-buy/30",
    Icon: TrendingUp,
  },
  BEARISH_TREND: {
    label: "Bearish Trend",
    text: "text-sell",
    chip: "bg-sell/15 border-sell/30",
    Icon: TrendingDown,
  },
  SIDEWAYS: {
    label: "Sideways",
    text: "text-muted",
    chip: "bg-surface-2 border-border",
    Icon: Waves,
  },
  HIGH_VOLATILITY: {
    label: "High Volatility",
    text: "text-hold",
    chip: "bg-hold/15 border-hold/30",
    Icon: Activity,
  },
  BREAKOUT: {
    label: "Breakout",
    text: "text-bull",
    chip: "bg-bull/15 border-bull/30",
    Icon: Zap,
  },
};

interface RegimeBadgeProps {
  regime: Regime;
  confidence: number;
  reasoning: string;
}

export function RegimeBadge({ regime, confidence, reasoning }: RegimeBadgeProps) {
  const style = REGIME_STYLE[regime];
  const { Icon } = style;

  return (
    <Card className="flex flex-col gap-4">
      <CardTitle>Market Regime</CardTitle>
      <div className="flex items-center gap-3">
        <span
          className={cn(
            "flex h-11 w-11 items-center justify-center rounded-xl border",
            style.chip,
            style.text
          )}
        >
          <Icon className="h-6 w-6" />
        </span>
        <div>
          <div className={cn("text-lg font-bold leading-tight", style.text)}>
            {style.label}
          </div>
          <div className="text-xs text-muted">
            {Math.round(confidence * 100)}% confidence
          </div>
        </div>
      </div>
      <p className="text-sm leading-relaxed text-slate-300">{reasoning}</p>
    </Card>
  );
}
