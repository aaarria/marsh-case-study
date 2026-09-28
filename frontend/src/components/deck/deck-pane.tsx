"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Plus, Presentation, Sparkles, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Kbd } from "@/components/ui/kbd";
import { GateBadge, StatusPill, ToneTag } from "@/components/status-badge";
import { SlideCanvas } from "@/components/slide-canvas";
import { SourceChip, SourceQuote } from "@/components/source-ref";
import { PassportSheet } from "@/components/passport-sheet";
import { InlineEdit } from "@/components/deck/inline-edit";
import { Matrix } from "@/components/matrix";
import type { Deck } from "@/components/deck/use-deck";
import { shortName } from "@/lib/format";
import { NODE_LABELS } from "@/lib/pipeline";
import type { AuditStatus, RunState, SlideBullet, SourceRef } from "@/lib/types";
import { cn } from "@/lib/utils";

const ISSUE = new Set<AuditStatus>(["CONTRADICTED", "NOT_FOUND"]);
const JUDGEMENT = new Set<AuditStatus>(["PARTIALLY_SUPPORTED", "UNCERTAIN"]);
const STEPS = ["research_company", "map_exposures", "compare_policies", "policy_fit_arena", "policy_check", "generate_pitch"];
const KIND_HINT: Record<string, string> = { policy: "Policy claim: audited against the cited brochure text", company: "Company statement", recommendation: "Recommendation / comparison", assumption: "Labelled assumption", marsh: "Marsh positioning" };

/** Before there is a deck: what is done and what is running, so the empty pane still informs. */
function Placeholder({ state }: { state: RunState }) {
  const last: Record<string, string> = {};
  for (const e of state.events) last[e.node] = e.status;
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      {state.run.status === "running" ? <Matrix variant="twinkle" dot={4} className="mb-4" title="Working" /> : (
        <span className="mb-3 flex size-9 items-center justify-center rounded-lg bg-raised text-muted-foreground">
          <Presentation className="size-4" />
        </span>
      )}
      <div className="text-sm font-medium text-ink">The deck appears here once drafted</div>
      <ol className="mt-4 space-y-1 text-left text-xs">
        {STEPS.map((n) => {
          const s = last[n];
          return (
            <li key={n} className={cn("flex items-center gap-2", s === "completed" ? "text-body" : s === "started" ? "text-ink" : "text-quiet")}>
              {s === "completed" ? <Check className="tone-ok tint-text size-3" /> : s === "started" ? <Matrix variant="scan" className="-m-px" /> : s === "failed" ? <X className="tone-danger tint-text size-3" /> : <span className="block size-3 text-center leading-3">·</span>}
              <span className={cn(s === "started" && "t-shimmer")}>{NODE_LABELS[n]}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function SourceSheet({ src, onClose }: { src: SourceRef | null; onClose: () => void }) {
  return (
    <Sheet open={!!src} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="thin-scroll w-full overflow-y-auto sm:max-w-lg">
        {src && (
          <>
            <SheetHeader className="pr-10">
              <SheetTitle>
                {shortName(src.policy_name) || src.policy_id} · p.{src.page}
              </SheetTitle>
              <SheetDescription>
                {src.section}
                {src.clause ? ` · ${src.clause}` : ""}
              </SheetDescription>
              <div className="pt-1">
                <SourceChip src={src} />
              </div>
            </SheetHeader>
            <div className="space-y-3 px-4 pb-6">
              <SourceQuote>{src.source_text}</SourceQuote>
              {src.linked_conditions.length > 0 && (
                <div>
                  <div className="kicker mb-1">Linked conditions</div>
                  <ul className="list-disc space-y-1 pl-4 text-xs text-body">
                    {src.linked_conditions.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                </div>
              )}
              <p className="text-xs text-quiet">Retrieval relevance is a ranking signal, not accuracy.</p>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** The deck, always visible: thumbnails, the open slide, and, while editing, that slide's fields. */
export function DeckPane({ state, deck }: { state: RunState; deck: Deck }) {
  const v = state.values;
  const pitch = v.pitch;
  const audit = deck.preview ?? v.audit;
  const [source, setSource] = useState<SourceRef | null>(null);
  const [aiBullet, setAiBullet] = useState<number | null>(null); // bullet index the ⌘K prompt bar is open on
  const canAiEdit = state.run.status === "awaiting_review" && state.question?.question === "review";

  const refsByChunk = useMemo(() => {
    const m: Record<string, SourceRef> = {};
    v.evidence_pack?.items.forEach((it) => it.sources.forEach((s) => (m[s.chunk_id] = s)));
    v.audit?.claims.forEach((c) => c.evidence.forEach((s) => (m[s.chunk_id] = s)));
    deck.preview?.claims.forEach((c) => c.evidence.forEach((s) => (m[s.chunk_id] = s)));
    return m;
  }, [v.evidence_pack, v.audit, deck.preview]);

  // A draft that lands while the run is working streams onto the slide; versions the advisor saved do not.
  const versionKey = pitch ? `${pitch.pitch_id}:${pitch.version}` : "";
  const [freshDraft, setFreshDraft] = useState<{ seen: string; live: string | null }>(() => ({ seen: versionKey, live: null }));
  if (versionKey !== freshDraft.seen) {
    const gen = state.events.findLast((e) => e.node === "generate_pitch" && e.status === "completed");
    const audited = state.events.findLast((e) => e.node === "audit_pitch" && e.status === "completed");
    const justDrafted = !!gen && (!audited || gen.created_at > audited.created_at);
    setFreshDraft({ seen: versionKey, live: justDrafted ? versionKey : null });
  }
  useEffect(() => {
    if (!freshDraft.live) return;
    const t = setTimeout(() => setFreshDraft((f) => ({ ...f, live: null })), 8000);
    return () => clearTimeout(t);
  }, [freshDraft.live]);

  const claimAt = (slideNo: number, bi: number) => audit?.claims.find((c) => c.claim.slide === slideNo && c.claim.bullet_index === bi);
  const countOn = (slideNo: number, set: Set<AuditStatus>) => (audit?.claims || []).filter((c) => c.claim.slide === slideNo && set.has(c.status)).length;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        if (!canAiEdit || deck.activeBullet == null) return;
        e.preventDefault();
        deck.setPassport(null); // the prompt bar replaces the passport sheet the click may have opened
        setAiBullet(deck.activeBullet);
        return;
      }
      if (t && ["INPUT", "TEXTAREA"].includes(t.tagName)) return;
      if (e.key === "ArrowRight") deck.go(deck.idx + 1);
      if (e.key === "ArrowLeft") deck.go(deck.idx - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [deck, canAiEdit]);

  if (!pitch) return <Placeholder state={state} />;
  const slides = deck.slides;
  const slide = slides[deck.idx];
  if (!slide) return null;
  const editing = !!deck.draft;
  const setBullet = (bi: number, text: string) => deck.patchSlide(deck.idx, { bullets: slide.bullets.map((b, k) => (k === bi ? { ...b, text } : b)) });
  const acceptAi = (bi: number, nb: SlideBullet) => {
    deck.startEdit(); // no-op when already editing; otherwise opens a draft to hold the accepted edit
    deck.patchSlide(deck.idx, { bullets: slide.bullets.map((b, k) => (k === bi ? { ...b, text: nb.text, kind: nb.kind, source_chunk_ids: nb.source_chunk_ids, source_urls: nb.source_urls ?? [], policy_id: nb.policy_id ?? null } : b)) });
    deck.setActiveBullet(bi);
    setAiBullet(null);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-hairline px-4 py-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
          <span className="font-medium text-ink">Draft v{pitch.version}</span>
          <GateBadge gate={audit?.summary.gate} size="xs" />
          <span className="text-xs text-muted-foreground">{slides.length} slides</span>
          {/* Last, so toggling edit mode never moves the badges before it. */}
          {editing && (
            <StatusPill tone={deck.dirty ? "warn" : "info"} size="xs" variant="solid">
              {deck.dirty ? "unsaved edits" : "editing"}
            </StatusPill>
          )}
        </div>
        <div className="flex items-center rounded-md border border-hairline-strong">
          <Button variant="ghost" size="icon-sm" onClick={() => deck.go(deck.idx - 1)} disabled={deck.idx === 0} aria-label="Previous slide">
            <ChevronLeft />
          </Button>
          <span className="min-w-14 text-center font-mono text-xs tabular-nums text-muted-foreground">
            {deck.idx + 1} / {slides.length}
          </span>
          <Button variant="ghost" size="icon-sm" onClick={() => deck.go(deck.idx + 1)} disabled={deck.idx >= slides.length - 1} aria-label="Next slide">
            <ChevronRight />
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <aside className="thin-scroll hidden w-40 shrink-0 overflow-y-auto border-r border-hairline p-2 md:block" aria-label="Slides">
          <ol className="space-y-2">
            {slides.map((s, i) => {
              const iss = countOn(s.slide_number, ISSUE);
              const wrn = countOn(s.slide_number, JUDGEMENT);
              const on = i === deck.idx;
              return (
                <li key={s.slide_number}>
                  <button type="button" onClick={() => deck.go(i)} aria-current={on ? "true" : undefined} className={cn("focus-ring w-full rounded-md p-1 text-left transition-colors duration-(--dur-fast)", on ? "bg-raised ring-1 ring-ink/70" : "hover:bg-raised/60")}>
                    <div className="slide-stage">
                      <div className="slide-stage-scale">
                        <SlideCanvas mode="thumb" slide={s} index={i} total={slides.length} disclaimer={pitch.disclaimer} refsByChunk={refsByChunk} className="shadow-none" />
                      </div>
                    </div>
                    <div className="mt-1 flex items-center justify-between gap-1 px-0.5 text-2xs text-muted-foreground">
                      <span className={cn("truncate", on && "text-ink")}>{i + 1}</span>
                      <span className="flex shrink-0 items-center gap-1">
                        {iss > 0 && <ToneTag tone="danger" title={`${iss} unsupported or contradicted`}>{iss}</ToneTag>}
                        {wrn > 0 && <ToneTag tone="warn" title={`${wrn} need judgement`}>{wrn}</ToneTag>}
                      </span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ol>
        </aside>

        <div className="thin-scroll flex min-w-0 flex-1 flex-col items-center overflow-y-auto px-3 py-4 sm:px-6 sm:py-5">
          <div className="w-full max-w-[960px] space-y-4">
            <div className="relative">
              <div className="slide-stage">
              <div className="slide-stage-scale">
              <SlideCanvas
                slide={slide}
                index={deck.idx}
                total={slides.length}
                disclaimer={pitch.disclaimer}
                refsByChunk={refsByChunk}
                statusFor={(bi) => {
                  const c = claimAt(slide.slide_number, bi);
                  return c && c.status !== "NOT_APPLICABLE" ? c.status : undefined;
                }}
                activeCite={deck.activeCite}
                onCite={(cid) => {
                  deck.setActiveCite(cid);
                  setSource(refsByChunk[cid] ?? null);
                }}
                activeBullet={deck.activeBullet}
                stream={freshDraft.live === versionKey}
                onBullet={(bi) => {
                  deck.setActiveBullet(bi);
                  const c = claimAt(slide.slide_number, bi);
                  if (c && !editing) deck.setPassport(c);
                }}
                className="shadow-3"
              />
              </div>
              </div>
              {aiBullet != null && slide.bullets[aiBullet] && <InlineEdit runId={state.run.run_id} slide={slide} bulletIndex={aiBullet} onAccept={(nb) => acceptAi(aiBullet, nb)} onClose={() => setAiBullet(null)} />}
            </div>
            <div className="flex items-center gap-1.5 text-2xs text-quiet">
              <span>Footnote → source · bullet → audit ·</span>
              <Kbd>←</Kbd>
              <Kbd>→</Kbd>
              {canAiEdit && (
                <>
                  <span>·</span>
                  <button type="button" className="focus-ring flex items-center gap-1 rounded-sm hover:text-body disabled:cursor-not-allowed disabled:opacity-60" disabled={deck.activeBullet == null} onClick={() => setAiBullet(deck.activeBullet)} title={deck.activeBullet == null ? "Click a bullet on the slide first" : "Edit the selected bullet with AI"}>
                    <Sparkles className="size-3" /> edit bullet with AI <Kbd>⌘K</Kbd>
                  </button>
                </>
              )}
            </div>

            {editing && (
              <div className="panel space-y-3 p-4">
                <div className="flex items-center justify-between">
                  <span className="kicker">Editing slide {deck.idx + 1}</span>
                  <span className="text-2xs text-quiet">Save from the review card in the thread</span>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label>Title</Label>
                    <Input value={slide.title} maxLength={200} onChange={(e) => deck.patchSlide(deck.idx, { title: e.target.value })} />
                  </div>
                  <div className="space-y-1">
                    <Label>Subtitle</Label>
                    <Input value={slide.subtitle || ""} maxLength={300} placeholder="Optional" onChange={(e) => deck.patchSlide(deck.idx, { subtitle: e.target.value || null })} />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>Bullets</Label>
                  {slide.bullets.map((b, bi) => (
                    <div key={bi} className={cn("rounded-md border border-hairline p-2 transition-colors", deck.activeBullet === bi && "border-hairline-bright")} onFocus={() => deck.setActiveBullet(bi)}>
                      <Textarea rows={2} value={b.text} autoFocus={deck.activeBullet === bi} onChange={(e) => setBullet(bi, e.target.value)} className="text-sm" />
                      <div className="mt-1 flex items-center justify-between text-2xs text-muted-foreground">
                        <span title={KIND_HINT[b.kind]}>
                          {b.kind}
                          {b.source_chunk_ids.length > 0 ? ` · ${b.source_chunk_ids.length} source(s) kept` : b.kind === "policy" ? " · no source: will fail the gate" : ""}
                        </span>
                        <Button type="button" variant="ghost" size="icon-xs" onClick={() => deck.patchSlide(deck.idx, { bullets: slide.bullets.filter((_, k) => k !== bi) })} aria-label="Remove bullet">
                          <Trash2 />
                        </Button>
                      </div>
                    </div>
                  ))}
                  <Button type="button" variant="outline" size="sm" onClick={() => deck.patchSlide(deck.idx, { bullets: [...slide.bullets, { text: "", source_chunk_ids: [], policy_id: null, kind: "recommendation" }] })}>
                    <Plus className="size-4" /> Add advisor note
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
      <SourceSheet src={source} onClose={() => setSource(null)} />
      <PassportSheet claim={deck.passport} onClose={() => deck.setPassport(null)} />
    </div>
  );
}
