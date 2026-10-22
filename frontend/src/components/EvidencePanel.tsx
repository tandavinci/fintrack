import { Card, CardTitle } from "@/components/ui/Card";

/**
 * Shows the raw numbers behind the signal. Each evidence string is "key=value"
 * from the quant engine (e.g. "momentum.RSI=46.5800"). We split on the first
 * '=' for a clean label/value layout. This is the "code computes" proof — every
 * number here came from the data snapshot, not a model.
 */
export function EvidencePanel({ evidence }: { evidence: string[] }) {
  return (
    <Card className="flex flex-col gap-4">
      <CardTitle>Evidence (from the data snapshot)</CardTitle>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2.5 sm:grid-cols-2">
        {evidence.map((item) => {
          const idx = item.indexOf("=");
          const key = idx >= 0 ? item.slice(0, idx) : item;
          const value = idx >= 0 ? item.slice(idx + 1) : "";
          return (
            <div
              key={item}
              className="flex items-center justify-between gap-3 border-b border-border/50 pb-2 text-sm"
            >
              <dt className="truncate text-muted" title={key}>
                {key}
              </dt>
              <dd className="font-mono font-medium text-slate-100">{value}</dd>
            </div>
          );
        })}
      </dl>
    </Card>
  );
}
