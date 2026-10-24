import { Card, CardTitle } from "@/components/ui/Card";
import type { ChartPoint } from "@/lib/api";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

function formatDate(d: string): string {
  // d is YYYY-MM-DD; show "12 Jan" style ticks.
  const date = new Date(d);
  if (Number.isNaN(date.getTime())) return d;
  return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}

interface TooltipEntry {
  name?: string;
  value?: number | string;
  color?: string;
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="glass rounded-lg px-3 py-2 text-xs shadow-card">
      <div className="mb-1 font-semibold text-slate-200">
        {label ? formatDate(label) : ""}
      </div>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ backgroundColor: entry.color }}
          />
          <span className="text-muted">{entry.name}</span>
          <span className="ml-auto font-medium text-slate-100">
            {typeof entry.value === "number" ? entry.value.toFixed(2) : entry.value}
          </span>
        </div>
      ))}
    </div>
  );
}

export function PriceChart({ data }: { data: ChartPoint[] }) {
  return (
    <Card className="flex h-full flex-col gap-4">
      <div className="flex items-center justify-between">
        <CardTitle>Price &amp; Moving Averages</CardTitle>
        <div className="flex items-center gap-4 text-xs text-muted">
          <Legend color="#6366f1" label="Close" />
          <Legend color="#22d3ee" label="SMA 20" />
          <Legend color="#f59e0b" label="SMA 50" />
        </div>
      </div>
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
            <defs>
              <linearGradient id="closeFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#6366f1" stopOpacity={0.35} />
                <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#263150" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="date"
              tickFormatter={formatDate}
              tick={{ fill: "#8b97b8", fontSize: 11 }}
              axisLine={{ stroke: "#263150" }}
              tickLine={false}
              minTickGap={32}
            />
            <YAxis
              domain={["auto", "auto"]}
              tick={{ fill: "#8b97b8", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={56}
            />
            <Tooltip content={<ChartTooltip />} />
            <Area
              type="monotone"
              dataKey="close"
              name="Close"
              stroke="#6366f1"
              strokeWidth={2}
              fill="url(#closeFill)"
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="sma20"
              name="SMA 20"
              stroke="#22d3ee"
              strokeWidth={1.5}
              dot={false}
              connectNulls
            />
            <Line
              type="monotone"
              dataKey="sma50"
              name="SMA 50"
              stroke="#f59e0b"
              strokeWidth={1.5}
              dot={false}
              connectNulls
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        className="inline-block h-2 w-2 rounded-full"
        style={{ backgroundColor: color }}
      />
      {label}
    </span>
  );
}
