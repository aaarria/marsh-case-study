import type { Tone } from "@/components/callout";
import { AUDIT_TONE, GATE_TONE, KIND_TONE, runStatus, titleCase } from "@/lib/format";
import type { AuditStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * StatusPill: the one way status is rendered. A small tinted pill with a solid dot
 * (Linear's status marker) so the meaning survives without colour. `variant="dot"`
 * drops the tint for dense tables.
 */
export function StatusPill({ tone, children, className, size = "sm", variant = "tint", title, dashed }: { tone: Tone; children: React.ReactNode; className?: string; size?: "xs" | "sm" | "md"; variant?: "tint" | "dot" | "solid"; title?: string; dashed?: boolean }) {
  return (
    <span
      title={title}
      className={cn(
        `tone-${tone} inline-flex w-fit shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border font-medium leading-none`,
        size === "xs" ? "h-[18px] px-1.5 text-2xs" : size === "sm" ? "h-5 px-1.5 text-xs" : "h-6 px-2 text-sm",
        variant === "tint" && "tint",
        variant === "dot" && "border-transparent bg-transparent px-0 text-body",
        variant === "solid" && "tint-strong",
        dashed && "border-dashed",
        className,
      )}
    >
      <span className="tint-dot size-1.5 shrink-0 rounded-full" aria-hidden />
      {children}
    </span>
  );
}
export function AuditBadge({ status, className, size }: { status: AuditStatus; className?: string; size?: "xs" | "sm" | "md" }) {
  return (
    <StatusPill tone={AUDIT_TONE[status] ?? "neutral"} className={className} size={size}>
      {titleCase(status.toLowerCase())}
    </StatusPill>
  );
}

export function GateBadge({ gate, className, size }: { gate?: string | null; className?: string; size?: "xs" | "sm" | "md" }) {
  if (!gate)
    return (
      <StatusPill tone="neutral" className={className} size={size} dashed>
        No audit
      </StatusPill>
    );
  return (
    <StatusPill tone={GATE_TONE[gate] ?? "neutral"} className={className} size={size} variant="solid">
      Gate {gate}
    </StatusPill>
  );
}

export function KindBadge({ kind, className, size = "xs" }: { kind: string; className?: string; size?: "xs" | "sm" | "md" }) {
  return (
    <StatusPill tone={KIND_TONE[kind] ?? "neutral"} className={cn("uppercase tracking-caps", className)} size={size}>
      {kind.replace("_", " ")}
    </StatusPill>
  );
}

export function RunStatusBadge({ status, errorKind, className, size }: { status: string; errorKind?: string | null; className?: string; size?: "xs" | "sm" | "md" }) {
  const s = runStatus({ status, error_kind: errorKind });
  return (
    <StatusPill tone={s.tone} className={className} size={size}>
      {s.label}
    </StatusPill>
  );
}

/** Generic labelled tag with tone; for validation/level/severity strings. */
export function ToneTag({ tone, children, className, size = "xs", title }: { tone: Tone; children: React.ReactNode; className?: string; size?: "xs" | "sm" | "md"; title?: string }) {
  return (
    <StatusPill tone={tone} className={className} size={size} title={title}>
      {children}
    </StatusPill>
  );
}
