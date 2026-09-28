"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
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
  const router = useRouter();
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  useEffect(() => {
    api.runs(30).then((r) => setRecent(r.runs)).catch(() => {});
  }, [runId, refreshKey]);
  async function removePitch(id: string) {
    setDeleteError(null);
    try {
      await api.deleteRun(id);
      setRecent((rows) => rows.filter((row) => row.run_id !== id));
      setPendingDelete(null);
      if (id === runId) router.push("/");
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Could not delete this pitch.");
    }
  }
  const r = useHealth();
  const h = r.health;
  const working = refreshKey === "running";

  return (
    <div className="flex h-dvh bg-canvas">
      <aside className="flex w-56 shrink-0 flex-col border-r border-[#e4ddd6] bg-canvas text-marsh-navy">
        <div className="flex h-16 items-center px-5">
          <Link href="/" className="focus-ring flex items-center rounded-md" aria-label="Marsh Health Policy Advisory, home">
            <Image src="/marsh-wordmark.png" alt="Marsh" width={1029} height={227} priority className="h-7 w-auto border-0 bg-transparent shadow-none" />
          </Link>
        </div>
        <div className="px-4 pb-3">
          <Link href="/" className="focus-ring flex h-10 items-center justify-center gap-1.5 rounded-md bg-marsh-navy px-3 text-sm font-medium text-marsh-white transition-[transform,opacity] duration-(--dur-fast) hover:bg-marsh-navy/90 active:scale-[0.985]">
            <Plus className="size-3.5" /> New pitch
          </Link>
        </div>
        <nav className="thin-scroll flex-1 overflow-y-auto px-2 pb-3" aria-label="Pitches">
          <div className="kicker px-2 pb-1 pt-2 text-marsh-navy/55">Pitches</div>
          {deleteError && <p className="px-2 pb-1 text-2xs text-marsh-navy">{deleteError}</p>}
          {recent.length === 0 ? (
            <p className="px-2 py-1 text-xs text-marsh-navy/55">Nothing yet.</p>
          ) : (
            <ul className="space-y-px">
              {recent.map((r) => {
                const rs = runStatus(r);
                const on = r.run_id === runId;
                const confirming = pendingDelete === r.run_id;
                return (
                  <li key={r.run_id} className="group flex items-center gap-0.5">
                    <Link href={`/runs/${r.run_id}`} aria-current={on ? "page" : undefined} title={`${r.company_name} · ${rs.label}`} className={cn("focus-ring flex h-8 min-w-0 flex-1 items-center gap-2 rounded-md px-2 text-sm transition-colors duration-(--dur-fast)", on ? "bg-marsh-navy text-marsh-white" : "text-marsh-navy hover:bg-marsh-navy/10")}>
                      <span className={cn(`tone-${rs.tone} tint-dot size-1.5 shrink-0 rounded-full`)} aria-hidden />
                      <span className="min-w-0 flex-1 truncate">{r.company_name}</span>
                      <span className={cn("shrink-0 text-2xs tabular-nums", on ? "text-marsh-white/70" : "text-marsh-navy/50")}>{r.status === "awaiting_review" ? "needs you" : fmtRelative(r.updated_at)}</span>
                    </Link>
                    <button
                      type="button"
                      aria-label={confirming ? `Confirm delete ${r.company_name}` : `Delete ${r.company_name}`}
                      title={confirming ? "Click again to delete" : "Delete pitch"}
                      className={cn("focus-ring grid size-7 shrink-0 place-items-center rounded-md", on ? "text-marsh-white/80 hover:bg-marsh-white/10" : "text-marsh-navy/40 hover:bg-marsh-navy/10 hover:text-marsh-navy")}
                      onClick={() => (confirming ? removePitch(r.run_id) : setPendingDelete(r.run_id))}
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        <footer className="flex h-8 shrink-0 items-center gap-3 border-t border-hairline bg-canvas px-4 text-2xs text-quiet" aria-label="System status">
          <span className="flex items-center gap-1.5">
            <Matrix variant="scan" state={r.error ? "error" : working ? "working" : r.llmOffline ? "idle" : "done"} title={r.error ? "API unreachable" : working ? "Working" : "Ready"} />
            <span className={cn(working && "t-shimmer")}>{r.error ? "API unreachable" : working ? "Working…" : r.loading ? "Connecting…" : "Ready"}</span>
          </span>
          {h && (
            <>
              <span className="text-hairline-bright">|</span>
              <span className="truncate" title={h.llm_provider === "groq" ? "Groq model" : "Gemini model"}>
                {h.llm_configured ? h.llm_model : "no model key"}
                {h.llm_configured && !h.model_free_tier_known && <span className="tone-warn tint-text"> · not on the free-tier list</span>}
              </span>
              <span className="text-hairline-bright">|</span>
              <span>web research {h.research_configured ? "on" : "off"}</span>
              <span className="text-hairline-bright">|</span>
              <span>
                {h.retrieval.ready ? `${h.retrieval.chunks ?? 0} chunks · ${h.retrieval.policies?.length ?? 0} brochures` : "indexing brochures…"}
              </span>
              <span className="ml-auto hidden truncate sm:inline">{h.llm_provider === "groq" ? "Groq" : "Gemini free tier"} · local FAISS + BM25 · nothing exported before approval</span>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}
