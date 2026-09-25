"use client";

import { createContext, useContext, useEffect, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Streaming text (Transitions.dev): each word is a `.t-stream-w` span that resolves through opacity and
 * a small blur as `.is-in` is added word by word. CSS lives in globals.css.
 *
 * Only text the system has just produced streams. `StreamScope` says whether the surrounding message
 * arrived after this client mounted the thread; history on page load, and bodies the advisor expands by
 * hand, rest visible. `live` overrides the scope. The decision is latched per text, so later re-renders
 * never restart or cut a stream short. Words are released together with a per-word transition-delay
 * (--stream-gap apart, compressed so long paragraphs finish within MAX_TOTAL_MS).
 */

/** True inside a message that arrived live (after mount); false for history and user-triggered reveals. */
export const StreamScope = createContext(false);
const GAP_MS = 60; // --stream-gap
const MAX_TOTAL_MS = 2_400; // long paragraphs speed up so nothing takes longer than this

export type StreamPart = { text: string; as?: "span" | "del" | "ins"; className?: string };

function tokens(text: string): string[] {
  return text.match(/\s*\S+\s*|\s+/g) ?? [];
}

export function Stream({ text, parts, live, className }: { text?: string; /** ordered segments (e.g. diff ops) streamed as one sequence */ parts?: StreamPart[]; live?: boolean; className?: string }) {
  const scope = useContext(StreamScope);
  const segs: StreamPart[] = parts ?? [{ text: text ?? "" }];
  const words = segs.map((p) => tokens(p.text));
  const total = words.reduce((n, w) => n + w.length, 0);
  const key = segs.map((p) => `${p.as ?? ""}|${p.text}`).join("\u0000");

  // Latched per text: whether to animate, and whether the words have been released yet.
  const [state, setState] = useState(() => ({ key, animate: live ?? scope, on: false }));
  if (state.key !== key) setState({ key, animate: live ?? scope, on: false });
  const { animate, on } = state;
  // Words paint hidden first (wiped: no transition, so a replaced text does not fade out), then all flip to
  // .is-in one frame later; per-word transition-delay does the staggering, so the stream's timing lives in
  // the compositor and is immune to throttled JS timers.
  useEffect(() => {
    if (!animate || on) return;
    const t = setTimeout(() => setState((s) => (s.key === key ? { ...s, on: true } : s)), 30);
    return () => clearTimeout(t);
  }, [animate, on, key]);

  const gap = Math.min(GAP_MS, MAX_TOTAL_MS / Math.max(1, total));
  // Global word index at which each part starts.
  const offsets = words.reduce<number[]>((acc, w, i) => (acc.push(i === 0 ? 0 : acc[i - 1] + words[i - 1].length), acc), []);

  return (
    <span className={cn("t-stream", className)}>
      {segs.map((p, pi) => {
        const Tag = p.as ?? "span";
        return (
          <Tag key={pi} className={p.className}>
            {words[pi].map((w, wi) => (
              <span key={wi} className={cn("t-stream-w", animate && !on && "is-wiped", (!animate || on) && "is-in")} style={animate ? { transitionDelay: `${Math.round((offsets[pi] + wi) * gap)}ms` } : undefined}>
                {w}
              </span>
            ))}
          </Tag>
        );
      })}
    </span>
  );
}
