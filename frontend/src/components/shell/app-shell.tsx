"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Matrix } from "@/components/matrix";
import { api } from "@/lib/api";
import { fmtRelative, runStatus } from "@/lib/format";
import type { RunSummary } from "@/lib/types";
import { useHealth } from "@/lib/use-health";
import { cn } from "@/lib/utils";

/**
 * Application frame: a thin rail of past pitches (chat-history style) and one content area.
 * There is nothing else to navigate: a pitch is a thread, and the thread carries every step.
 * `refreshKey` re-reads the rail when the open run changes status. A status bar along the bottom
 * says which Gemini model is doing the work, whether web research is on, and what is indexed.
 */
export function AppShell({ children, runId, refreshKey }: { children: React.ReactNode; runId?: string | null; refreshKey?: string | null }) {
  const [recent, setRecent] = useState<RunSummary[]>([]);
  useEffect(() => {
    api.runs(30).then((r) => setRecent(r.runs)).catch(() => {});
  }, [runId, refreshKey]);
  const r = useHealth();
  const h = r.health;
  const working = refreshKey === "running";

  return (
    <div className="flex h-dvh bg-canvas">
      <aside className="flex w-56 shrink-0 flex-col border-r border-[#e4ddd6] bg-marsh-cream text-marsh-navy">
        <div className="flex h-12 items-center px-4">
          <Link href="/" className="focus-ring flex items-center gap-2 rounded-md" aria-label="Marsh Advisory — home">
            <Image src="/marsh.png" alt="Marsh McLennan" width={1024} height={84} priority className="h-4 w-auto" />
          </Link>
        </div>
        <div className="px-3 pb-2">
          <Link href="/" className="focus-ring flex h-8 items-center justify-center gap-1.5 rounded-md bg-marsh-navy text-sm font-medium text-marsh-white transition-[transform,opacity] duration-(--dur-fast) hover:opacity-90 active:scale-[0.985]">
            <Plus className="size-3.5" /> New pitch
          </Link>
        </div>
        <nav className="thin-scroll flex-1 overflow-y-auto px-2 pb-3" aria-label="Pitches">
          <div className="kicker px-2 pb-1 pt-2 text-marsh-navy/55">Pitches</div>
          {recent.length === 0 ? (
            <p className="px-2 py-1 text-xs text-marsh-navy/55">Nothing yet.</p>
          ) : (
            <ul className="space-y-px">
              {recent.map((r) => {
                const rs = runStatus(r);
                const on = r.run_id === runId;
                return (
                  <li key={r.run_id}>
                    <Link href={`/runs/${r.run_id}`} aria-current={on ? "page" : undefined} title={`${r.company_name} · ${rs.label}`} className={cn("focus-ring flex h-8 items-center gap-2 rounded-md px-2 text-sm transition-colors duration-(--dur-fast)", on ? "bg-marsh-navy text-marsh-white" : "text-marsh-navy hover:bg-marsh-navy/10")}>
                      <span className={cn(`tone-${rs.tone} tint-dot size-1.5 shrink-0 rounded-full`)} aria-hidden />
                      <span className="min-w-0 flex-1 truncate">{r.company_name}</span>
                      <span className={cn("shrink-0 text-2xs tabular-nums", on ? "text-marsh-white/70" : "text-marsh-navy/50")}>{r.status === "awaiting_review" ? "needs you" : fmtRelative(r.updated_at)}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        <footer className="flex h-6 shrink-0 items-center gap-3 border-t border-hairline bg-panel px-3 text-2xs text-quiet" aria-label="System status">
          <span className="flex items-center gap-1.5">
            <Matrix variant="scan" state={r.error ? "error" : working ? "working" : r.llmOffline ? "idle" : "done"} title={r.error ? "API unreachable" : working ? "Working" : "Ready"} />
            <span className={cn(working && "t-shimmer")}>{r.error ? "API unreachable" : working ? "Working…" : r.loading ? "Connecting…" : "Ready"}</span>
          </span>
          {h && (
            <>
              <span className="text-hairline-bright">|</span>
              <span className="truncate" title="GEMINI_MODEL — the only model used; never switched">
                {h.llm_configured ? h.llm_model : "no Gemini key"}
                {h.llm_configured && !h.model_free_tier_known && <span className="tone-warn tint-text"> · not on the free-tier list</span>}
              </span>
              <span className="text-hairline-bright">|</span>
              <span>web research {h.research_configured ? "on" : "off"}</span>
              <span className="text-hairline-bright">|</span>
              <span>
                {h.retrieval.ready ? `${h.retrieval.chunks ?? 0} chunks · ${h.retrieval.policies?.length ?? 0} brochures` : "indexing brochures…"}
              </span>
              <span className="ml-auto hidden truncate sm:inline">Gemini free tier · local FAISS + BM25 · nothing exported before approval</span>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}
