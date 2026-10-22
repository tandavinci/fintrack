import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";

interface TickerSelectProps {
  value: string;
  options: readonly string[];
  onChange: (value: string) => void;
  disabled?: boolean;
}

/** A clean native select styled to match the dashboard. */
export function TickerSelect({
  value,
  options,
  onChange,
  disabled,
}: TickerSelectProps) {
  return (
    <div className="relative">
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "appearance-none rounded-xl bg-surface-2 border border-border",
          "px-4 py-2.5 pr-10 text-sm font-medium text-slate-100",
          "focus:outline-none focus:ring-2 focus:ring-brand/60",
          "disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
        )}
      >
        {options.map((opt) => (
          <option key={opt} value={opt} className="bg-surface-2">
            {opt}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted" />
    </div>
  );
}
