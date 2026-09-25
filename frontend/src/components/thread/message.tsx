"use client";

import { useContext, useState } from "react";
import { ChevronRight, type LucideIcon } from "lucide-react";
import { type AgentState, Matrix } from "@/components/matrix";
import { Stream, StreamScope } from "@/components/stream";
import { cn } from "@/lib/utils";

/**
 * Chat primitives. The system speaks in plain blocks aligned left (no avatar noise); the advisor's
 * inputs and answers are right-aligned bubbles, so a glance separates "what it did" from "what I said".
 * Completed steps are `ToolRow`s: one line each, like an agent's tool calls, expandable for the detail.
 */

export function ToolRow({ icon: Icon, summary, meta, children, defaultOpen = false, className }: { icon: LucideIcon; summary: React.ReactNode; meta?: React.ReactNode; children?: React.ReactNode; defaultOpen?: boolean; className?: string }) {
  const [open, setOpen] = useState(defaultOpen);
  const expandable = !!children;
  const live = useContext(StreamScope);
  return (
    <div className={cn("group/row -mx-2 rounded-md", className)}>
      <button
        type="button"
        onClick={expandable ? () => setOpen((o) => !o) : undefined}
        aria-expanded={expandable ? open : undefined}
        className={cn("focus-ring flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm", expandable ? "hover:bg-raised/60" : "cursor-default")}
      >
        <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-body">{typeof summary === "string" ? <Stream text={summary} /> : summary}</span>
        {meta && <span className="shrink-0 text-2xs tabular-nums text-quiet">{meta}</span>}
        {expandable && <ChevronRight className={cn("size-3.5 shrink-0 text-quiet transition-transform duration-(--dur-fast)", open && "rotate-90")} aria-hidden />}
      </button>
      {expandable && open && (
        // A body the system opened itself streams with its row; one the advisor expands by hand is simply shown.
        <StreamScope value={live && defaultOpen}>
          <div className="ml-[1.125rem] space-y-2 border-l border-hairline py-2 pl-3.5 pr-2 text-sm leading-relaxed text-body">{children}</div>
        </StreamScope>
      )}
    </div>
  );
}

/** The step that is running now: the dot matrix scanning beside a shimmering label, as a live row. */
export function WorkingRow({ label, detail, meta, state = "working", children }: { label: React.ReactNode; detail?: React.ReactNode; meta?: React.ReactNode; state?: AgentState; children?: React.ReactNode }) {
  return (
    <div className="-mx-2 rounded-md px-2 py-1">
      <div className="flex items-center gap-2 text-sm">
        <Matrix variant="scan" state={state} />
        <span className={cn("min-w-0 flex-1 truncate font-medium", state === "working" ? "t-shimmer" : "text-ink")}>{label}</span>
        {meta && <span className="shrink-0 text-2xs tabular-nums text-quiet">{meta}</span>}
      </div>
      {detail && <p className="ml-[1.375rem] mt-0.5 text-xs text-muted-foreground">{detail}</p>}
      {children && <div className="ml-[1.375rem] mt-2">{children}</div>}
    </div>
  );
}
export function AssistantMessage({ title, meta, children, tone, className }: { title?: React.ReactNode; meta?: React.ReactNode; children?: React.ReactNode; tone?: string; className?: string }) {
  return (
    <div className={cn("group/msg relative pl-4", className)}>
      <span className={cn("absolute left-0 top-[0.55rem] size-1.5 rounded-full", tone ? `${tone} tint-dot` : "bg-hairline-bright")} aria-hidden />
      {(title || meta) && (
        <div className="flex items-baseline justify-between gap-3">
          {title && <div className="text-sm font-medium text-ink">{title}</div>}
          {meta && <div className="shrink-0 text-2xs tabular-nums text-quiet">{meta}</div>}
        </div>
      )}
      {children && <div className="mt-1 space-y-2 text-sm leading-relaxed text-body">{children}</div>}
    </div>
  );
}

export function UserMessage({ children, meta, className }: { children: React.ReactNode; meta?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-end gap-1", className)}>
      <div className="max-w-[92%] rounded-2xl rounded-br-md bg-raised px-3.5 py-2 text-sm leading-relaxed text-ink">{children}</div>
      {meta && <div className="pr-1 text-2xs text-quiet">{meta}</div>}
    </div>
  );
}

/** A question the system is asking right now: framed, so it reads as the one thing that needs the advisor. */
export function QuestionFrame({ title, children, className }: { title: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-xl border border-hairline-strong bg-surface p-4 shadow-1", className)} role="region" aria-label="Question">
      <div className="text-sm font-medium text-ink">{title}</div>
      <div className="mt-2 space-y-3 text-sm text-body">{children}</div>
    </div>
  );
}

export function ChipRow({ items, className }: { items: React.ReactNode[]; className?: string }) {
  if (!items.length) return null;
  return (
    <div className={cn("flex flex-wrap gap-1", className)}>
      {items.map((it, i) => (
        <span key={i} className="rounded-md border border-hairline px-1.5 py-0.5 text-xs text-body">
          {it}
        </span>
      ))}
    </div>
  );
}
