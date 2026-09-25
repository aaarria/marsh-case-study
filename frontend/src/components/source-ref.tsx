"use client";

import { useState } from "react";
import { FileText } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { SourceRef } from "@/lib/types";
import { shortName } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Quoted brochure passage. */
export function SourceQuote({ children, className }: { children: React.ReactNode; className?: string }) {
  return <blockquote className={cn("thin-scroll max-h-72 overflow-auto whitespace-pre-wrap rounded-md border-l-2 border-ink/60 bg-raised px-3 py-2.5 text-sm leading-relaxed text-body", className)}>{children}</blockquote>;
}

/** Linked condition / footnote line item. */
export function ConditionNote({ children }: { children: React.ReactNode }) {
  return <li className="tone-warn tint rounded-md border px-2.5 py-1.5 text-xs text-body">{children}</li>;
}

export function SourceChip({ src, className }: { src: SourceRef; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className={cn("focus-ring inline-flex h-5 max-w-full items-center gap-1 rounded-sm border border-hairline bg-raised px-1.5 text-2xs text-body transition-colors hover:border-hairline-bright hover:text-ink", className)} title="Open source">
        <FileText className="size-3 shrink-0 text-muted-foreground" />
        <span className="truncate">
          {shortName(src.policy_name) || src.policy_id} p.{src.page}
          {src.section ? ` · ${src.section.slice(0, 28)}` : ""}
        </span>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="text-base">
              {src.policy_name || src.policy_id} — page {src.page}
            </DialogTitle>
            <DialogDescription className="flex flex-wrap gap-2 text-xs">
              {src.section && <span>Section: {src.section}</span>}
              {src.clause && <span>Clause: {src.clause}</span>}
              {src.content_type && <span className="rounded-sm bg-raised px-1.5 font-mono text-2xs text-body">{src.content_type}</span>}
              {typeof src.retrieval_relevance === "number" && <span title="Ranking signal only">retrieval relevance {src.retrieval_relevance.toFixed(3)} (not accuracy)</span>}
            </DialogDescription>
          </DialogHeader>
          <SourceQuote>{src.source_text}</SourceQuote>
          {src.linked_conditions?.length ? <p className="text-xs text-muted-foreground">This passage carries {src.linked_conditions.length} linked footnote{src.linked_conditions.length === 1 ? "" : "s"}; the audit passport quotes them.</p> : null}
          <div className="font-mono text-2xs text-quiet">chunk {src.chunk_id}</div>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function SourceList({ sources, max = 3 }: { sources: SourceRef[]; max?: number }) {
  if (!sources?.length) return <span className="text-xs text-quiet">No source</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {sources.slice(0, max).map((s) => (
        <SourceChip key={s.chunk_id} src={s} />
      ))}
      {sources.length > max && <span className="self-center text-2xs text-quiet">+{sources.length - max}</span>}
    </div>
  );
}
