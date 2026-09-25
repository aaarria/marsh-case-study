"use client";

import Image from "next/image";
import type { AuditStatus, Slide, SlideBullet, SourceRef } from "@/lib/types";
import { Stream } from "@/components/stream";
import { shortName } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Per-slide footnote numbering: unique chunk ids in order of first appearance. */
function slideCitations(slide: Slide): string[] {
  const seen: string[] = [];
  for (const b of slide.bullets) for (const c of b.source_chunk_ids) if (!seen.includes(c)) seen.push(c);
  return seen;
}

const ISSUE: AuditStatus[] = ["CONTRADICTED", "NOT_FOUND"];
const WARN: AuditStatus[] = ["PARTIALLY_SUPPORTED", "UNCERTAIN"];

type Item = { b: SlideBullet; i: number };
const LEFT_HEADERS: Record<string, string> = { company: "What we understand", policy: "Key benefits", recommendation: "Our view", marsh: "Why Marsh" };
const LEAD_ORDER = ["recommendation", "policy", "company", "marsh"];

/**
 * Same split as the PPTX builder (`_plan_columns`): the brief's two-column layout when a slide
 * mixes statements with assumptions, or carries at least two "Watch-out" items; otherwise one card.
 */
function planColumns(slide: Slide): { left: Item[]; right: Item[]; leftHeader?: string; rightHeader?: string } {
  const items: Item[] = slide.bullets.map((b, i) => ({ b, i }));
  const watch = items.filter((x) => /^watch-out/i.test(x.b.text));
  if (watch.length >= 2 && watch.length < items.length) {
    return { left: items.filter((x) => !watch.includes(x)), right: watch, leftHeader: "How it compares", rightHeader: "What to watch" };
  }
  const left = items.filter((x) => x.b.kind !== "assumption");
  const right = items.filter((x) => x.b.kind === "assumption");
  if (!left.length || !right.length) return { left: items, right: [] };
  const counts = new Map<string, number>();
  for (const x of left) counts.set(x.b.kind, (counts.get(x.b.kind) ?? 0) + 1);
  const lead = [...counts.entries()].sort((a, b) => b[1] - a[1] || LEAD_ORDER.indexOf(a[0]) - LEAD_ORDER.indexOf(b[0]))[0][0];
  return { left, right, leftHeader: LEFT_HEADERS[lead] ?? "Our view", rightHeader: "Assumptions to confirm" };
}

interface SlideCanvasProps {
  slide: Slide;
  index: number;
  total: number;
  disclaimer?: string;
  refsByChunk?: Record<string, SourceRef>;
  statusFor?: (bulletIndex: number) => AuditStatus | undefined;
  mode?: "thumb" | "full";
  activeCite?: string | null;
  onCite?: (chunkId: string) => void;
  activeBullet?: number | null;
  onBullet?: (bulletIndex: number) => void;
  /** stream the bullets in word by word (a draft that has just been generated) */
  stream?: boolean;
  className?: string;
}

/**
 * Renders one slide at 16:9 in Marsh's template, geometry-for-geometry with the exported PPTX
 * (`pptx_builder.py`): navy header band with a Georgia title and the white wordmark; either an
 * open two-column body with ruled bold headers and a hairline divider, or a single card on the
 * light canvas; en-dash bullets in Calibri; a numbered "Sources" block pinned above the footer;
 * footer rule with the navy wordmark, disclaimer, copyright and page number. Sizes are
 * container-relative so the same component serves 140px thumbnails and the full canvas.
 */
export function SlideCanvas({ slide, index, total, disclaimer, refsByChunk = {}, statusFor, mode = "full", activeCite, onCite, activeBullet, onBullet, stream = false, className }: SlideCanvasProps) {
  const cites = slideCitations(slide);
  const num = (id: string) => cites.indexOf(id) + 1;
  const thumb = mode === "thumb";
  const { left, right, leftHeader, rightHeader } = planColumns(slide);
  const twoCol = right.length > 0;

  // Density: the PPTX fits body text 16 → 12pt to the space left above the sources; mirror that
  // from the character load of the fuller column (Tailwind's `number` type only accepts quarter steps).
  const load = (xs: Item[]) => xs.reduce((n, x) => n + x.b.text.length + 40, 0);
  const chars = twoCol ? Math.max(load(left), load(right)) * 2 : load(left);
  const bodySize = chars > 900 ? "text-cq-1.25" : chars > 560 ? "text-cq-1.5" : "text-cq-1.75";

  const bullet = ({ b, i }: Item, header?: string) => {
    const st = statusFor?.(i);
    const issue = st && ISSUE.includes(st);
    const warn = st && WARN.includes(st);
    const active = activeBullet === i;
    const interactive = !!onBullet && !thumb;
    let text = b.text;
    if (header === "What to watch") text = text.replace(/^watch-out:\s*/i, "");
    const assumption = b.kind === "assumption";
    if (assumption) text = text.replace(/^assumption:\s*/i, "");
    return (
      <li
        key={i}
        onClick={interactive ? () => onBullet(i) : undefined}
        className={cn(
          "slide-bullet rounded-[0.3cqw] pr-[1%] leading-[1.3]",
          interactive && "cursor-pointer transition-colors hover:bg-slide-hover",
          active && "bg-slide-accent-tint",
          !thumb && issue && "tone-danger tint",
          !thumb && warn && "tone-warn tint",
          !thumb && (issue || warn) && "border-0 text-slide-body",
        )}
      >
        {assumption && <span className="font-bold text-slide-muted">Assumption: </span>}
        {stream ? <Stream live text={text} /> : text}
        {!thumb &&
          b.source_chunk_ids.map((cid) => (
            <button
              key={cid}
              type="button"
              className="cite ml-[0.25em]"
              data-active={activeCite === cid}
              title={refsByChunk[cid] ? `${shortName(refsByChunk[cid].policy_name) || refsByChunk[cid].policy_id} p.${refsByChunk[cid].page}` : cid}
              onClick={(e) => {
                e.stopPropagation();
                onCite?.(cid);
              }}
            >
              {num(cid)}
            </button>
          ))}
        {thumb && b.source_chunk_ids.length > 0 && <sup className="ml-[0.2em] text-[0.62em] font-semibold text-slide-accent">{b.source_chunk_ids.map(num).join(",")}</sup>}
      </li>
    );
  };

  const column = (items: Item[], header?: string) => (
    <div className="flex min-h-0 flex-col">
      {header && <div className="mb-[0.9cqw] shrink-0 border-b-[0.14cqw] border-slide-ink px-[0.4cqw] pb-[0.5cqw] text-cq-1.75 font-bold leading-none text-slide-ink">{header}</div>}
      <ul className={cn("min-h-0 flex-1 space-y-[0.7cqw] overflow-hidden", bodySize)}>{items.map((x) => bullet(x, header))}</ul>
    </div>
  );

  const sources = (cites.length > 0 || slide.footnote) && (
    <div className="mt-[1.2cqw] shrink-0 leading-snug text-slide-muted">
      <div className="text-cq-0.75 font-bold uppercase tracking-wide">Sources</div>
      {cites.length > 0 && (
        <ol className={cn("mt-[0.3cqw] grid gap-x-[3%] text-cq-1", cites.length > 4 ? "grid-cols-2" : "grid-cols-1")}>
          {cites.map((cid, i) => {
            const r = refsByChunk[cid];
            return (
              <li key={cid} className="truncate">
                <span className="font-semibold">{i + 1}.</span> {r ? `${r.policy_name || r.policy_id}, p.${r.page}${r.section ? `, ${r.section}` : ""}` : cid}
              </li>
            );
          })}
        </ol>
      )}
      {slide.footnote && <div className="mt-[0.3cqw] truncate text-cq-1">{slide.footnote}</div>}
    </div>
  );

  const subtitle = slide.subtitle && <div className="mb-[1.2cqw] shrink-0 text-cq-1.25 leading-snug text-slide-muted">{slide.subtitle}</div>;

  const map = slide.layout === "map";
  const mapItems = slide.bullets.map((b, i) => ({ b, i }));
  const exposures = mapItems.filter((x) => x.b.kind === "company" || x.b.kind === "assumption").slice(0, 3);
  const benefits = mapItems.filter((x) => x.b.kind === "policy").slice(0, 3);
  const why = mapItems.find((x) => x.b.kind === "recommendation" || x.b.kind === "marsh");
  const mapRows = Math.max(exposures.length, benefits.length, 1);

  const mapCard = (item: Item | undefined, side: "exposure" | "benefit") => {
    if (!item) return <div />;
    const assumption = item.b.kind === "assumption";
    const text = item.b.text.replace(/^(assumption|why this policy):\s*/i, "");
    return (
      <div className="flex min-h-0 flex-col justify-center border border-slide-rule bg-slide-bg px-[1.2cqw] py-[0.7cqw]" style={{ borderLeftWidth: "0.35cqw", borderLeftColor: side === "exposure" ? "var(--slide-sky)" : "var(--slide-accent)" }}>
        <div className={cn("text-cq-0.75 font-bold uppercase tracking-wide", side === "benefit" ? "text-slide-accent" : "text-slide-muted")}>{assumption ? "Assumption" : side === "benefit" ? "Brochure benefit" : "From the profile"}</div>
        <div className="mt-[0.3cqw] line-clamp-3 text-cq-1.25 leading-snug text-slide-ink">
          {text}
          {side === "benefit" && !thumb &&
            item.b.source_chunk_ids.map((cid) => (
              <button key={cid} type="button" className="cite ml-[0.25em]" data-active={activeCite === cid} onClick={(e) => { e.stopPropagation(); onCite?.(cid); }}>
                {num(cid)}
              </button>
            ))}
        </div>
      </div>
    );
  };

  return (
    <div className={cn("slide-frame select-none", !thumb && "select-text", className)} aria-label={`Slide ${index + 1} of ${total}: ${slide.title}`}>
      {/* Header band: 0.9in of 7.5in */}
      <header className="absolute inset-x-0 top-0 flex h-[12%] items-center justify-between bg-slide-ink pl-[3.75%] pr-[3.75%]">
        <h2 className="truncate pr-[3%] font-slide-serif text-cq-2 leading-none font-normal tracking-normal text-white">{slide.title}</h2>
        <Image src="/marsh-white.png" alt="Marsh McLennan" width={1024} height={84} className="w-[18cqw] h-auto shrink-0" />
      </header>

      {/* Body: between the header band and the footer rule */}
      <div className={cn("absolute inset-x-0 top-[12%] bottom-[5.4%]", (!twoCol || map) && "bg-slide-canvas")}>
        {map ? (
          <div className="flex h-full flex-col px-[3.75%] pt-[1.6cqw] pb-[1cqw]">
            {subtitle}
            <div className="grid grid-cols-[1fr_auto_1fr] gap-x-[1.2cqw] text-cq-0.75 font-bold uppercase tracking-wide text-slide-ocean">
              <div>Client exposure</div>
              <div />
              <div>Stated in the brochure</div>
            </div>
            <div className="mt-[0.6cqw] grid min-h-0 flex-1 grid-cols-[1fr_auto_1fr] gap-x-[1.2cqw] gap-y-[0.7cqw]">
              {Array.from({ length: mapRows }, (_, r) => (
                <div key={r} className="contents">
                  {mapCard(exposures[r], "exposure")}
                  <div className="flex items-center text-slide-ocean">
                    {exposures[r] && benefits[r] ? <span aria-hidden className="text-cq-1.75 leading-none">→</span> : null}
                  </div>
                  {mapCard(benefits[r], "benefit")}
                </div>
              ))}
            </div>
            {why && <div className="mt-[0.8cqw] shrink-0 bg-slide-ink px-[1.4cqw] py-[0.7cqw] text-cq-1.25 leading-snug font-semibold text-white">{why.b.text}</div>}
            {sources}
          </div>
        ) : twoCol ? (
          <div className="flex h-full flex-col px-[3.75%] pt-[2.4cqw] pb-[1cqw]">
            {subtitle}
            <div className="grid min-h-0 flex-1 grid-cols-2 divide-x divide-slide-rule">
              <div className="min-h-0 pr-[2.2cqw]">{column(left, leftHeader)}</div>
              <div className="min-h-0 pl-[2.2cqw]">{column(right, rightHeader)}</div>
            </div>
            {sources}
          </div>
        ) : (
          <div className="absolute inset-x-[3%] top-[4.8%] bottom-[3.2%] flex flex-col border border-slide-rule bg-slide-bg px-[2.6cqw] pt-[1.9cqw] pb-[1.4cqw]">
            {subtitle}
            <div className="min-h-0 flex-1">{column(left)}</div>
            {sources}
          </div>
        )}
      </div>

      {/* Footer: rule at 7.1in, wordmark, disclaimer, copyright, page */}
      <footer className="absolute inset-x-0 bottom-0 flex h-[5.4%] items-center border-t border-slide-ink pl-[3%] pr-[2.5%] text-slide-ink">
        <Image src="/marsh.png" alt="Marsh McLennan" width={1024} height={84} className="w-[14.25cqw] h-auto shrink-0" />
        <span className="ml-[2.2cqw] min-w-0 flex-1 truncate text-cq-0.75 leading-none text-slide-muted">{disclaimer}</span>
        <span className="ml-[2cqw] shrink-0 truncate text-cq-1 leading-none">Copyright © {new Date().getFullYear()} Marsh. All rights reserved.</span>
        <span className="ml-[2cqw] w-[3cqw] shrink-0 text-right text-cq-1 leading-none tabular-nums">{index + 1}</span>
      </footer>
    </div>
  );
}
