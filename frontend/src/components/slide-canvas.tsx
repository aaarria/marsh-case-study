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
          {cites.map((cid) => {
            const r = refsByChunk[cid];
            return (
              <li key={cid} className="truncate">
                {r ? `Source: ${r.policy_name || r.policy_id}, p. ${r.page}${r.section ? `, section "${r.section}"` : ""}` : cid}
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
  const reasons = parsed.filter((x) => x !== score && x.label.toUpperCase() !== "TRADEOFF" && x.b.kind !== "assumption" && (x.b.kind === "recommendation" || x.b.kind === "policy")).slice(0, 5);
  const trade = parsed.find((x) => x.label.toUpperCase() === "TRADEOFF" || (x.b.kind === "assumption" && x !== score));

  let body: ReactNode;
  const priorities = parsed.filter((p) => p.label.toUpperCase() === "PRIORITY");
  const weight = parsed.find((p) => p.label.toUpperCase() === "WEIGHT");
  const considered = parsed.filter((p) => p.label.toUpperCase() === "CONSIDERED");
  const assessed = parsed.flatMap((p) => (
    p.label.toUpperCase() === "ASSESSED"
      ? p.body.split("|").map((part) => part.trim()).filter(Boolean).map((body) => ({ ...p, body }))
      : []
  ));
  const whyNote = parsed.find((p) => p.label.toUpperCase() === "WHY");
  const lens = parsed.find((p) => p.label.toUpperCase() === "LENS");
  const profileSlide = glanceFacts.filter((f) => ["INDUSTRY", "SCALE", "FOOTPRINT"].includes(f.label.toUpperCase())).length >= 3;
  if ((slide.layout === "glance" || profileSlide) && (priorities.length > 0 || lens || considered.length > 0)) {
    const known = glanceFacts.filter((f) => f.body && f.body.toLowerCase() !== "not established");
    body = (
      <div className="flex h-full flex-col px-[4.6%] pt-[0.4cqw] pb-[1cqw]">
        <div className="grid min-h-0 flex-1 grid-cols-[1.35fr_0.8fr] gap-[2cqw]">
          <div className="min-h-0 space-y-[0.8cqw] overflow-hidden">
            {priorities.slice(0, 2).map((p) => (
              <div key={p.i} className="border-l-[0.35cqw] border-[#B6E8F4] pl-[1cqw]">
                <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-ink">Client priority</div>
                <div className="font-slide-serif text-cq-1.75 leading-tight text-slide-ink">{p.body.split("|")[0]}</div>
                {p.body.includes("|") && <div className="mt-[0.3cqw] text-cq-1 leading-snug text-slide-body">{p.body.split("|").slice(1).join("|")}</div>}
              </div>
            ))}
            {weight && (
              <div>
                <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-ink">Priority weight</div>
                <div className="font-slide-serif text-cq-2 leading-tight text-slide-ink">{weight.body}</div>
              </div>
            )}
            {whyNote && !/no specific (client )?priority/i.test(whyNote.body) && <p className="line-clamp-5 text-cq-1.25 leading-snug text-slide-body">{whyNote.body}</p>}
            {considered.length > 0 && (
              <div className="pl-[1.3cqw]">
                <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-ink">What was considered</div>
                <ul className="mt-[0.3cqw] space-y-[0.2cqw]">
                  {considered.slice(0, 6).map((item) => (
                    <li key={item.i} className="text-cq-1 leading-snug text-slide-body">{item.body}</li>
                  ))}
                </ul>
              </div>
            )}
            {known.length > 0 && (
              <div className="space-y-[0.45cqw]">
                {known.map((f) => (
                  <div key={f.i}>
                    <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-ink">{f.label}</div>
                    <div className="text-cq-1.25 leading-snug text-slide-body">{f.body}</div>
                  </div>
                ))}
              </div>
            )}
            {!priorities.length && <p className="text-cq-1.25 leading-snug text-slide-body">No specific client priority was selected. The comparison therefore uses the standard baseline coverage criteria.</p>}
          </div>
          <div className="min-h-0 overflow-hidden border-l-[0.35cqw] border-[#B6E8F4] pl-[1cqw]">
            <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-ink">Decision lens</div>
            {lens && <p className="mt-[0.4cqw] line-clamp-4 text-cq-1.25 leading-snug text-slide-body">{/no specific client priority was selected/i.test(lens.body) ? "The supplied brochures are compared on hospitalisation, room rent, restore, waiting periods, non-medical expenses, and co-payment." : lens.body}</p>}
            {assessed.length > 0 && (
              <div className="mt-[0.8cqw]">
                <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-ink">What was assessed</div>
                <ul className="mt-[0.3cqw] space-y-[0.15cqw]">
                  {assessed.slice(0, 7).map((item) => (
                    <li key={item.body} className="text-cq-1 leading-snug text-slide-body">{item.body}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  } else if (slide.layout === "glance" || profileSlide) {
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
        {slide.subtitle && <p className="mb-[0.6cqw] text-cq-1 leading-snug text-slide-muted">{slide.subtitle}</p>}
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
                <div className="text-cq-1 leading-snug text-slide-muted">{ref ? `Source: ${ref.policy_name || ref.policy_id}, p. ${ref.page}${ref.section ? `, section "${ref.section}"` : ""}` : ""}</div>
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
      <div className="flex h-full flex-col px-[4.6%] pt-[0.6cqw] pb-[1cqw]">
        {slide.subtitle && <p className="mb-[0.5cqw] text-cq-1 leading-snug text-slide-muted">{slide.subtitle}</p>}
        <div className="grid min-h-0 flex-1 grid-cols-2">
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
      </div>
    );
  } else if (slide.layout === "comparison") {
    const header = parsed.find((p) => p.label.toUpperCase() === "COLUMNS");
    const rec = parsed.find((p) => p.label.toUpperCase() === "REC");
    const names = header ? header.body.split("|").map((s) => s.trim()).filter(Boolean) : [];
    const highlight = rec ? Math.max(names.indexOf(rec.body), 0) : 0;
    const rows = parsed.filter((p) => p.b.text.startsWith("ROW|")).slice(0, 8).filter((row) => {
      const cells = row.b.text.split("|").slice(2).map((s) => s.trim());
      return cells.some((cell) => !/no specific priority/i.test(cell));
    });
    const cols = Math.max(names.length, 1);
    const columns = `minmax(0, 1.15fr) repeat(${cols}, minmax(0, 1fr))`;
    body = (
      <div className="flex h-full flex-col px-[3.2%] pt-[0.2cqw] pb-[0.2cqw]">
        <div className="grid shrink-0 gap-[0.4cqw]" style={{ gridTemplateColumns: columns }}>
          <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-muted">Brochure</div>
          {names.map((name, n) => (
            <div key={name} className={n === highlight ? "bg-[#B6E8F4] px-[0.35cqw] py-[0.25cqw] text-slide-ink" : "px-[0.35cqw] py-[0.25cqw]"}>
              {n === highlight && <div className="text-cq-0.75 font-bold uppercase tracking-wide">Recommended</div>}
              <div className="line-clamp-3 font-slide-serif text-cq-1.25 font-bold leading-tight text-slide-ink">{name}</div>
            </div>
          ))}
        </div>
        <div className="mt-[0.3cqw] grid min-h-0 flex-1" style={{ gridTemplateRows: `repeat(${Math.max(rows.length, 1)}, minmax(0, 1fr))` }}>
          {rows.map((row) => {
            const parts = row.b.text.split("|").slice(1).map((s) => s.trim());
            return (
              <div key={row.i} className="grid min-h-0 items-center gap-[0.4cqw] border-t border-slide-rule" style={{ gridTemplateColumns: columns }}>
                <div className="line-clamp-2 text-cq-1.25 font-semibold leading-tight text-slide-ink">{parts[0]}</div>
                {Array.from({ length: cols }, (_, n) => (
                  <div key={n} className={n === highlight ? "line-clamp-2 bg-[#B6E8F4]/50 px-[0.3cqw] text-cq-1.25 leading-tight text-slide-ink" : "line-clamp-2 text-cq-1.25 leading-tight text-slide-body"}>
                    {parts[n + 1]}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    );
  } else if (slide.layout === "why" || slide.layout === "decision") {
    const policy = parsed.find((p) => p.label.toUpperCase() === "POLICY");
    const points = parsed.filter((p) => !["POLICY", "ALTERNATIVE", "COLUMNS"].includes(p.label.toUpperCase()) && !p.b.text.startsWith("Alternative|")).slice(0, 6).map((p) => ({
      ...p,
      body: /no specific priority selected/i.test(p.body)
        ? "No client priority was selected. The recommendation follows the standard coverage criteria in the supplied brochures."
        : p.body.replace(/^•\s*/, ""),
    }));
    body = (
      <div className="flex h-full flex-col px-[4.6%] pt-[0.2cqw] pb-[0.3cqw]">
        {policy && (
          <div className="mb-[0.4cqw] shrink-0 border-l-[0.35cqw] border-slide-sky pl-[0.8cqw]">
            <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-ink">Recommended brochure</div>
            <div className="font-slide-serif text-cq-1.75 font-bold leading-tight text-slide-ink">{policy.body}</div>
          </div>
        )}
        {slide.subtitle && !/no specific priority|standard coverage requirements|stated client priorities/i.test(slide.subtitle) && <p className="mb-[0.3cqw] shrink-0 text-cq-1 leading-snug text-slide-muted">{slide.subtitle}</p>}
        <div className="grid min-h-0 flex-1 grid-cols-2 content-start gap-x-[1.4cqw] gap-y-[0.5cqw] overflow-hidden">
          {points.map((p) => {
            const ref = refsByChunk[p.b.source_chunk_ids[0]];
            return (
              <div key={p.i} className="min-h-0 border-l-[0.3cqw] border-[#B6E8F4] pl-[0.7cqw]">
                <div className="text-cq-0.75 font-bold uppercase leading-tight text-slide-ink">{p.label}</div>
                <div className="line-clamp-3 text-cq-1 leading-snug text-slide-body">{p.body}</div>
                {ref && <div className="truncate text-cq-0.75 text-slide-muted">Source: {ref.policy_name || ref.policy_id}, p. {ref.page}{ref.section ? `, section "${ref.section}"` : ""}</div>}
              </div>
            );
          })}
        </div>
      </div>
    );
  } else if (slide.layout === "recommendation") {
    const name = (slide.subtitle || slide.title || "").replace(/^recommended policy:\s*/i, "");
    body = (
      <div className="flex h-full flex-col px-[4.6%] pt-[0.6cqw] pb-[1cqw]">
        <div className="font-slide-serif text-cq-2.25 leading-tight text-slide-ink">{name}</div>
        <div className="mt-[1cqw] min-h-0 flex-1 space-y-[0.8cqw] overflow-hidden">
          {reasons.map((r) => (
            <div key={r.i}>
              <div className="text-cq-1 font-semibold leading-tight text-slide-ink">{r.label}</div>
              <div className="text-cq-1.25 leading-snug text-slide-body">{r.body || r.label}{citeMarks(r)}</div>
            </div>
          ))}
          {trade && (
            <div className="pt-[0.4cqw]">
              <div className="text-cq-0.75 font-bold uppercase tracking-wide text-slide-muted">Key trade-off</div>
              <div className="mt-[0.2cqw] text-cq-1.25 leading-snug text-slide-body">{trade.body || trade.label}</div>
            </div>
          )}
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

  if (slide.layout === "cover") {
    const name = slide.bullets[0]?.text || "";
    const when = slide.bullets[1]?.text || "";
    const kicker = slide.bullets[2]?.text || "Prepared by Marsh McLennan";
    return (
      <div className={cn("slide-frame select-none bg-slide-bg", !thumb && "select-text", className)} aria-label={`Slide ${index + 1} of ${total}: ${slide.title}`}>
        <div className="pointer-events-none absolute inset-[1.6%] border border-slide-ink" />
        <Image src="/marsh.png" alt="Marsh" width={1176} height={400} className="absolute left-[4.9%] top-[4.7%] h-[5.3%] w-auto" />
        <div className="absolute left-[4.9%] right-[6%] top-[22%]">
          <div className="font-slide-serif text-cq-2.75 font-bold leading-tight text-slide-ink">{slide.title}</div>
          {slide.subtitle && <div className="mt-[0.8cqw] max-w-[70%] text-cq-1.25 leading-snug text-slide-muted">{slide.subtitle}</div>}
          <div className="mt-[1.6cqw] font-slide-serif text-cq-2 font-bold leading-tight text-slide-ink">{name}</div>
          <div className="mt-[1.2cqw] h-[0.22cqw] w-[7cqw] bg-[#B6E8F4]" />
          <div className="mt-[0.7cqw] text-cq-1.25 text-slide-ink">{kicker}</div>
          {when && <div className="mt-[0.4cqw] text-cq-1 text-slide-muted">{when}</div>}
        </div>
        <footer className="absolute inset-x-0 bottom-0 flex h-[6%] items-center border-t border-slide-rule px-[4.9%] text-slide-ink">
          <span className="shrink-0 text-cq-0.75 font-semibold leading-none">Marsh McLennan</span>
          <span className="min-w-0 flex-1 truncate text-cq-0.75 leading-none text-slide-muted">Policy evidence. Client priorities. Advisory recommendation.</span>
          <span className="ml-[2cqw] shrink-0 text-cq-1 leading-none tabular-nums">{String(index + 1).padStart(2, "0")}</span>
        </footer>
      </div>
    );
  }

  return (
    <div className={cn("slide-frame select-none", !thumb && "select-text", className)} aria-label={`Slide ${index + 1} of ${total}: ${slide.title}`}>
      <div className="pointer-events-none absolute inset-[1.6%] border border-slide-ink" />
      <Image src="/marsh.png" alt="Marsh" width={1176} height={400} className="absolute left-[4.9%] top-[4.7%] h-[5.3%] w-auto" />
      <h2 className="absolute left-[4.9%] right-[6%] top-[11%] truncate font-slide-serif text-cq-2.25 font-bold leading-none text-slide-ink">{slide.title}</h2>
      <div className="absolute inset-x-[4.9%] top-[18%] h-px bg-[#B6E8F4]" />
      <div className="absolute inset-x-0 top-[20%] bottom-[7%] overflow-hidden bg-slide-canvas">{body}</div>

      {/* Footer: rule at 7.1in, wordmark, disclaimer, copyright, page */}
      <footer title={disclaimer} className="absolute inset-x-0 bottom-0 flex h-[7%] items-center border-t border-slide-rule px-[4.6%] text-slide-ink">
        <span className="shrink-0 text-cq-0.75 font-semibold leading-none text-slide-ink">Marsh McLennan</span>
        <span className="min-w-0 flex-1 truncate text-cq-0.75 leading-none text-slide-muted">Policy evidence. Client priorities. Advisory recommendation.</span>
        <span className="ml-[2cqw] shrink-0 text-cq-1 leading-none tabular-nums text-slide-ink">{String(index + 1).padStart(2, "0")}</span>
      </footer>
    </div>
  );
}
