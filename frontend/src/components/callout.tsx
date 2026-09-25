import { AlertTriangle, CheckCircle2, Info, OctagonAlert, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

const ICON: Partial<Record<Tone, LucideIcon>> = { ok: CheckCircle2, warn: AlertTriangle, danger: OctagonAlert, info: Info, neutral: Info };

/**
 * Callout: tinted message block for state that needs the reader's attention
 * (gate results, quota pauses, generation notes). Tone drives colour; content stays ink.
 */
export function Callout({ tone = "neutral", title, icon, children, actions, className, compact }: { tone?: Tone; title?: React.ReactNode; icon?: LucideIcon | null; children?: React.ReactNode; actions?: React.ReactNode; className?: string; compact?: boolean }) {
  const Icon = icon === null ? null : icon || ICON[tone] || Info;
  return (
    <div role={tone === "danger" || tone === "warn" ? "alert" : undefined} className={cn(`tone-${tone} tint flex gap-3 rounded-lg border`, compact ? "px-3 py-2" : "px-4 py-3", className)}>
      {Icon && <Icon className="tint-text mt-0.5 size-4 shrink-0" />}
      <div className="min-w-0 flex-1 space-y-1.5">
        {title && <div className="text-sm font-medium text-ink">{title}</div>}
        {children && <div className="text-sm leading-relaxed text-body [&_a]:underline [&_a]:underline-offset-2">{children}</div>}
        {actions && <div className="flex flex-wrap items-center gap-2 pt-1">{actions}</div>}
      </div>
    </div>
  );
}
