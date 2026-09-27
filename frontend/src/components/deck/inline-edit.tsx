"use client";

import { useEffect, useRef, useState } from "react";
import { Check, CornerDownLeft, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Matrix } from "@/components/matrix";
import { api, ApiError } from "@/lib/api";
import { Stream } from "@/components/stream";
import { wordDiff } from "@/lib/diff";
import type { Slide, SlideBullet } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * ⌘K on a bullet: a prompt bar over the slide. The instruction goes to Gemini with the run's evidence
 * pack; the proposal comes back as a word diff against the current text with Accept / Reject, like an
 * editor's inline edit. Accepting only patches the local draft; saving and re-auditing stay explicit.
 */
export function InlineEdit({ runId, slide, bulletIndex, onAccept, onClose }: { runId: string; slide: Slide; bulletIndex: number; onAccept: (b: SlideBullet) => void; onClose: () => void }) {
  const bullet = slide.bullets[bulletIndex];
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<{ bullet: SlideBullet; note: string | null; audit?: { status: string; detail: string } } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);

  const run = async () => {
    const t = instruction.trim();
    if (!bullet || t.length < 2 || busy) return;
    setBusy(true);
    setError(null);
    try {
      setProposal(await api.rewrite(runId, { slide_number: slide.slide_number, instruction: t, text: bullet.text, kind: bullet.kind, source_chunk_ids: bullet.source_chunk_ids, source_urls: bullet.source_urls ?? [] }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const blocked = proposal?.audit && ["CONTRADICTED", "NOT_FOUND", "UNAVAILABLE"].includes(proposal.audit.status);
  const accept = () => proposal && !blocked && onAccept(proposal.bullet);

  if (!bullet) return null;
  const ops = proposal ? wordDiff(bullet.text, proposal.bullet.text) : null;
  const unchanged = proposal && proposal.bullet.text.trim() === bullet.text.trim();

  return (
    <div
      role="dialog"
      aria-label={`Edit bullet ${bulletIndex + 1} with AI`}
      className="panel absolute inset-x-3 bottom-3 z-10 space-y-2 p-3 shadow-3"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        } else if (e.key === "Enter" && proposal && !unchanged && !blocked) {
          // ↵ accepts from anywhere in the proposal view; Refine/Reject keep their own Enter when focused.
          if (e.target instanceof HTMLButtonElement && !e.target.dataset.accept) return;
          e.preventDefault();
          accept();
        }
      }}
    >
      <div className="flex items-center gap-2">
        <Matrix variant="pulse" state={busy ? "working" : proposal ? "done" : "idle"} />
        <span className="kicker flex-1">
          Edit bullet {bulletIndex + 1} with AI <span className="text-quiet">· slide {slide.slide_number}</span>
        </span>
        <Button variant="ghost" size="icon-xs" onClick={onClose} aria-label="Close">
          <X />
        </Button>
      </div>

      {proposal && ops ? (
        <>
          <p className={cn("text-sm leading-relaxed", unchanged && "text-muted-foreground")}>
            <Stream
              live
              parts={ops.map((op) =>
                op.type === "same"
                  ? { text: op.text }
                  : op.type === "del"
                    ? { text: op.text, as: "del" as const, className: "tone-danger tint-text rounded-sm bg-danger/10 no-underline line-through decoration-danger/70" }
                    : { text: op.text, as: "ins" as const, className: "tone-ok tint-text rounded-sm bg-ok/10 no-underline" },
              )}
            />
          </p>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted-foreground">
            <span>{proposal.bullet.kind}</span>
            <span>{proposal.bullet.source_chunk_ids.length ? `${proposal.bullet.source_chunk_ids.length} source(s) cited` : proposal.bullet.kind === "policy" ? "no source" : "no source needed"}</span>
            {bullet.source_chunk_ids.length > 0 && proposal.bullet.source_chunk_ids.length < bullet.source_chunk_ids.length && <span className="tone-warn tint-text">drops a citation</span>}
            {proposal.note && <span className="tone-warn tint-text">{proposal.note}</span>}
            {proposal.audit && <span className={blocked ? "tone-danger tint-text" : ""}>Audit: {proposal.audit.status.replaceAll("_", " ").toLowerCase()}{proposal.audit.detail ? `: ${proposal.audit.detail}` : ""}</span>}
            {unchanged && <span>No change proposed.</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={accept} disabled={!!unchanged || !!blocked} autoFocus data-accept>
              <Check className="size-4" /> Accept <Kbd className="ml-1 border-white/20 bg-white/10 text-canvas">↵</Kbd>
            </Button>
            <Button size="sm" variant="outline" onClick={() => setProposal(null)}>
              Refine
            </Button>
            <Button size="sm" variant="ghost" onClick={onClose}>
              Reject <Kbd className="ml-1">esc</Kbd>
            </Button>
            <span className="ml-auto text-2xs text-quiet">Accepted edits stay unsaved until you save &amp; re-audit.</span>
          </div>
        </>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run();
          }}
          className="space-y-2"
        >
          <p className="line-clamp-2 text-xs text-muted-foreground">{bullet.text}</p>
          <div className="flex items-center gap-2">
            <input
              ref={inputRef}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              maxLength={500}
              disabled={busy}
              placeholder="Tell it what to change… e.g. shorter · mention the waiting period · plain English"
              aria-label="Instruction"
              className="h-8 min-w-0 flex-1 rounded-md border border-hairline bg-transparent px-2.5 text-sm text-ink outline-none placeholder:text-quiet focus:border-hairline-bright disabled:cursor-not-allowed"
            />
            <Button type="submit" size="sm" loading={busy} disabled={instruction.trim().length < 2}>
              <CornerDownLeft className="size-4" /> Propose
            </Button>
          </div>
          <p className="text-2xs text-quiet">{error ? <span className="tone-danger tint-text">{error}</span> : "Policy statements can only cite this run's brochure evidence; a request the evidence cannot support is refused, not invented."}</p>
        </form>
      )}
    </div>
  );
}
