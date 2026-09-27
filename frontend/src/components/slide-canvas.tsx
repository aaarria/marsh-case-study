"use client";

import type { ReactNode } from "react";
import Image from "next/image";
import type { AuditStatus, Slide, SlideBullet, SourceRef } from "@/lib/types";
import { Stream } from "@/components/stream";
import { shortName } from "@/lib/format";
import { splitBullet } from "@/lib/slide-text";
import { cn } from "@/lib/utils";

/** Per-slide footnote numbering: unique chunk ids in order of first appearance. */
function slideCitations(slide: Slide): string[] {
  const seen: string[] = [];
  for (const b of slide.bullets) for (const c of b.source_chunk_ids) if (!seen.includes(c)) seen.push(c);
  return seen;
}

function clientLine(title: string, subtitle: string | null | undefined, facts: { label: string; body: string }[]): string {
  if (subtitle && subtitle.trim().split(/\s+/).length >= 8) return subtitle.trim();
  const by = Object.fromEntries(facts.map((f) => [f.label.toUpperCase(), f.body]));
  const ok = (v?: string) => (v && !["unknown", "not established", "none", "n/a"].includes(v.trim().toLowerCase()) ? v.trim().replace(/\.$/, "") : "");
  const industry = ok(by.INDUSTRY);
  const scale = ok(by.SCALE);
  const footprint = ok(by.FOOTPRINT);
  const bits: string[] = [];
  if (industry) {
    const word = industry[0].toLowerCase() + industry.slice(1);
    bits.push(word.startsWith("a ") || word.startsWith("an ") ? word : `a ${word} business`);
  }
  if (scale) bits.push(scale[0].toLowerCase() + scale.slice(1));
  if (footprint) bits.push(footprint[0].toLowerCase() + footprint.slice(1));
  if (!bits.length) return "Company facts are still thin. The points below are working assumptions for the medical programme.";
  const company = title.replace(/\s+at a glance$/i, "").trim() || "This client";
  const sentence = `${company} is ${bits.join(", ")}`;
  return sentence.endsWith(".") ? sentence : `${sentence}.`;
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

  const parsed = slide.bullets.map((b, i) => ({ b, i, ...splitBullet(b.text) }));
  const citeMarks = (item: Item) =>
    !thumb &&
    item.b.source_chunk_ids.map((cid) => (
      <button key={cid} type="button" className="cite ml-[0.25em]" data-active={activeCite === cid} onClick={(e) => { e.stopPropagation(); onCite?.(cid); }}>
        {num(cid)}
      </button>
    ));

  const glanceFacts = parsed.filter((x) => ["INDUSTRY", "SCALE", "FOOTPRINT"].includes(x.label.toUpperCase())).slice(0, 3);
  const glanceCards = parsed.filter((x) => !glanceFacts.includes(x) && (x.b.kind === "company" || x.b.kind === "assumption")).slice(0, 4);
  const mapExposures = parsed.filter((x) => x.b.kind === "company" || x.b.kind === "assumption").slice(0, 3);
  const mapBenefits = parsed.filter((x) => x.b.kind === "policy").slice(0, 3);
  const why = parsed.find((x) => x.b.kind === "recommendation");
  const marshLines = parsed.filter((x) => x.b.kind === "marsh").slice(0, 3);
  const watches = parsed.filter((x) => x.b.kind !== "marsh").slice(0, 4);
  const score = parsed.find((x) => x.label.toUpperCase() === "SCORE" || /\d+(?:\.\d+)?\s*\/\s*100/.test(x.b.text));
  const scoreValue = (score?.body || score?.b.text || "").match(/(\d+(?:\.\d+)?)\s*\/\s*100/)?.[1]?.split(".")[0];
  const reasons = parsed.filter((x) => x !== score && x.label.toUpperCase() !== "TRADEOFF" && x.b.kind !== "assumption" && (x.b.kind === "recommendation" || x.b.kind === "policy")).slice(0, 3);
  const trade = parsed.find((x) => x.label.toUpperCase() === "TRADEOFF" || (x.b.kind === "assumption" && x !== score));

  let body: ReactNode;
  if (slide.layout === "glance") {
    body = (
      <div className="flex h-full flex-col px-[4.6%] pt-[1cqw] pb-[1cqw]">
        <div className="grid min-h-0 flex-1 grid-cols-[1.15fr_1fr] gap-[3cqw]">
          <p className="font-slide-serif text-cq-1.5 leading-snug text-slide-ink">{clientLine(slide.title, slide.subtitle, glanceFacts)}</p>
          <div className="space-y-[1.2cqw]">
            {glanceFacts.map((f) => (
              <div key={f.i}>
                <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-muted">{f.label}</div>
                <div className="text-cq-1.25 leading-snug text-slide-ink">{f.body}{citeMarks(f)}{f.b.kind === "assumption" && <div className="text-cq-0.75 text-slide-muted">Assumption</div>}</div>
              </div>
            ))}
          </div>
        </div>
        {glanceCards.length > 0 && (
          <div className="mt-[1cqw] grid shrink-0 grid-cols-4 gap-[1.6cqw] border-t border-slide-rule pt-[1cqw]">
            {glanceCards.map((c) => (
              <div key={c.i}>
                <div className="mb-[0.4cqw] h-[0.35cqw] w-full bg-slide-sky" />
                <div className="font-slide-serif text-cq-1.25 leading-tight text-slide-ink">{c.label}</div>
                {c.body && <div className="mt-[0.3cqw] line-clamp-3 text-cq-1 leading-snug text-slide-body">{c.body}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  } else if (slide.layout === "map") {
    const rows = Math.max(mapExposures.length, mapBenefits.length, 1);
    body = (
      <div className="flex h-full flex-col px-[4.6%] pt-[1cqw] pb-[1cqw]">
        <div className="grid grid-cols-[1.1fr_1.2fr_1.2fr_1fr] gap-[1cqw] text-cq-0.75 font-bold uppercase tracking-wide text-slide-muted">
          <div>Exposure</div><div>Client need</div><div>Policy benefit</div><div>Evidence</div>
        </div>
        <div className="mt-[0.6cqw] min-h-0 flex-1 space-y-[0.7cqw]">
          {Array.from({ length: rows }, (_, r) => {
            const exp = mapExposures[r];
            const ben = mapBenefits[r];
            const ref = ben && refsByChunk[ben.b.source_chunk_ids[0]];
            return (
              <div key={r} className="grid grid-cols-[1.1fr_1.2fr_1.2fr_1fr] gap-[1cqw] border-t border-slide-rule pt-[0.5cqw]">
                <div className="font-slide-serif text-cq-1.25 leading-tight text-slide-ink">{exp?.label}</div>
                <div className="text-cq-1 leading-snug text-slide-body">{exp?.body || (exp ? "Identified for this client" : "")}</div>
                <div className="text-cq-1 leading-snug text-slide-ink">{ben?.label}{ben && citeMarks(ben)}</div>
                <div className="text-cq-1 leading-snug text-slide-muted">{ref ? `${ref.policy_name || ref.policy_id}, p.${ref.page}` : ""}</div>
              </div>
            );
          })}
        </div>
        <div className="mt-[0.8cqw] shrink-0 bg-slide-ink px-[1.4cqw] py-[0.8cqw] text-white">
          <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-sky">Why this policy fits</div>
          <div className="mt-[0.2cqw] text-cq-1.25 leading-snug">{why ? why.body || why.label : "Selected on the evidence in this deck. Advisor review is still required."}</div>
        </div>
      </div>
    );
  } else if (slide.layout === "perspective") {
    body = (
      <div className="grid h-full grid-cols-2 px-[4.6%] pt-[1cqw] pb-[1cqw]">
        <div className="space-y-[1.4cqw] pr-[2cqw]">
          {marshLines.map((m) => (
            <div key={m.i}>
              <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-ink">{m.label}</div>
              <div className="mt-[0.2cqw] text-cq-1.25 leading-snug text-slide-body">{m.body || m.label}</div>
            </div>
          ))}
        </div>
        <div className="space-y-[1cqw] border-l border-slide-rule pl-[2cqw]">
          {watches.map((w) => (
            <div key={w.i} className="grid grid-cols-[5.5cqw_1fr] gap-[0.8cqw]">
              <div className="h-fit bg-slide-sky px-[0.3cqw] py-[0.15cqw] text-center text-cq-0.75 font-bold text-slide-ink">{w.label.toUpperCase()}</div>
              <div className="text-cq-1.25 leading-snug text-slide-ink">{w.body || w.label}{citeMarks(w)}</div>
            </div>
          ))}
        </div>
      </div>
    );
  } else if (slide.layout === "recommendation") {
    const name = (slide.subtitle || slide.title || "").replace(/^recommended policy:\s*/i, "");
    body = (
      <div className="flex h-full flex-col px-[4.6%] pt-[0.6cqw] pb-[1cqw]">
        <div className="font-slide-serif text-cq-2.25 leading-tight text-slide-ink">{name}</div>
        <div className="mt-[1cqw] grid min-h-0 flex-1 grid-cols-[1fr_16cqw] gap-[2cqw]">
          <div className="space-y-[1cqw]">
            {reasons.map((r, n) => (
              <div key={r.i} className="grid grid-cols-[2.2cqw_1fr] gap-[0.8cqw]">
                <div className="font-slide-serif text-cq-1.5 text-slide-muted">{String(n + 1).padStart(2, "0")}</div>
                <div className="text-cq-1.25 leading-snug text-slide-ink">{r.body || r.label}{citeMarks(r)}</div>
              </div>
            ))}
            {trade && (
              <div className="pt-[0.4cqw]">
                <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-muted">Key trade-off</div>
                <div className="mt-[0.2cqw] text-cq-1.25 leading-snug text-slide-body">{trade.body || trade.label}</div>
              </div>
            )}
          </div>
          <div className="h-fit bg-slide-ink px-[1.2cqw] py-[1cqw] text-white">
            <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-sky">Fit score</div>
            <div className="font-slide-serif text-cq-3 leading-none">{scoreValue || "—"}</div>
            <div className="mt-[0.4cqw] text-cq-1">Decision-support only</div>
          </div>
        </div>
      </div>
    );
  } else {
    body = twoCol ? (
      <div className="flex h-full flex-col px-[3.75%] pt-[2.4cqw] pb-[1cqw]">
        {slide.subtitle && <div className="mb-[1.2cqw] shrink-0 text-cq-1.25 leading-snug text-slide-muted">{slide.subtitle}</div>}
        <div className="grid min-h-0 flex-1 grid-cols-2 divide-x divide-slide-rule">
          <div className="min-h-0 pr-[2.2cqw]">{column(left, leftHeader)}</div>
          <div className="min-h-0 pl-[2.2cqw]">{column(right, rightHeader)}</div>
        </div>
        {sources}
      </div>
    ) : (
      <div className="absolute inset-x-[3%] top-[4.8%] bottom-[3.2%] flex flex-col border border-slide-rule bg-slide-bg px-[2.6cqw] pt-[1.9cqw] pb-[1.4cqw]">
        {slide.subtitle && <div className="mb-[1.2cqw] shrink-0 text-cq-1.25 leading-snug text-slide-muted">{slide.subtitle}</div>}
        <div className="min-h-0 flex-1">{column(left)}</div>
        {sources}
      </div>
    );
  }

  return (
    <div className={cn("slide-frame select-none", !thumb && "select-text", className)} aria-label={`Slide ${index + 1} of ${total}: ${slide.title}`}>
      <header className="absolute inset-x-0 top-0 flex h-[16%] items-end justify-between bg-slide-bg px-[4.6%] pb-[1cqw]">
        <div className="min-w-0">
          <div className="text-cq-0.75 font-semibold tracking-wide text-slide-muted">{String(index + 2).padStart(2, "0")}</div>
          <h2 className="truncate font-slide-serif text-cq-2.5 leading-none font-normal text-slide-ink">{slide.title}</h2>
        </div>
        <Image src="/marsh.png" alt="Marsh McLennan" width={1024} height={84} className="w-[14cqw] h-auto shrink-0" />
      </header>

      {/* Body: between the header band and the footer rule */}
      <div className="absolute inset-x-0 top-[16%] bottom-[7%] overflow-hidden bg-slide-canvas">{body}</div>

      {/* Footer: rule at 7.1in, wordmark, disclaimer, copyright, page */}
      <footer title={disclaimer} className="absolute inset-x-0 bottom-0 flex h-[7%] items-center border-t border-slide-rule px-[4.6%] text-slide-ink">
        <span className="min-w-0 flex-1 truncate text-cq-0.75 leading-none text-slide-muted">Brochure evidence. Policy wording prevails.</span>
        <span className="ml-[2cqw] shrink-0 text-cq-1 leading-none tabular-nums text-slide-ink">{String(index + 2).padStart(2, "0")}</span>
      </footer>
    </div>
  );
}
