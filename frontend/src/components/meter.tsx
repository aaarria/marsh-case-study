import { cn } from "@/lib/utils";
import type { Tone } from "./callout";

/** Thin horizontal meter, 0..1. Tone colours the fill; default is ink. */
export function Meter({ value, tone, className, size = "sm", label }: { value: number; tone?: Tone; className?: string; size?: "xs" | "sm" | "md"; label?: string }) {
  const v = Math.max(0, Math.min(1, value || 0));
  return (
    <div role="meter" aria-valuenow={Math.round(v * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={label} className={cn("w-full overflow-hidden rounded-full bg-raised", size === "xs" ? "h-1" : size === "sm" ? "h-1.5" : "h-2", className)}>
      <div className={cn("h-full rounded-full transition-[width] duration-(--dur-slow) ease-(--ease-out)", tone ? `tone-${tone} tint-dot` : "bg-ink")} style={{ width: `${v * 100}%` }} />
    </div>
  );
}

