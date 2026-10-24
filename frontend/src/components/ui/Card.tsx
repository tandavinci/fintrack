import { cn } from "@/lib/utils";
import type { HTMLAttributes } from "react";

/** A soft, rounded surface card — the base building block of the dashboard. */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "glass rounded-xl2 shadow-card p-5 sm:p-6 transition-colors",
        className
      )}
      {...props}
    />
  );
}

export function CardTitle({
  className,
  ...props
}: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn(
        "text-xs font-semibold uppercase tracking-widest text-muted",
        className
      )}
      {...props}
    />
  );
}
