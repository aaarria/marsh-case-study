"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { AuditReport, ClaimAudit, Pitch, Slide } from "@/lib/types";

/**
 * Shared deck state for one run: which slide is open, the unsaved draft (if editing), the
 * preview audit of that draft, and which bullet / citation is in focus. Owned by the run page so
 * the review question in the thread and the deck pane act on the same object.
 */
export function useDeck(pitch: Pitch | undefined) {
  const [rawIdx, setIdx] = useState(0);
  const [draft, setDraft] = useState<Slide[] | null>(null);
  const [preview, setPreview] = useState<AuditReport | null>(null);
  const [activeBullet, setActiveBullet] = useState<number | null>(null);
  const [activeCite, setActiveCite] = useState<string | null>(null);
  const [passport, setPassport] = useState<ClaimAudit | null>(null);

  // A new pitch version (after save / regenerate) supersedes any local draft: reset during render, not in an effect.
  const versionKey = pitch ? `${pitch.pitch_id}:${pitch.version}` : "";
  const [seenVersion, setSeenVersion] = useState(versionKey);
  if (versionKey !== seenVersion) {
    setSeenVersion(versionKey);
    setDraft(null);
    setPreview(null);
  }

  const pitchSlides = pitch?.slides;
  const slides = useMemo(() => draft ?? pitchSlides ?? [], [draft, pitchSlides]);
  const total = slides.length;
  const idx = Math.min(rawIdx, Math.max(0, total - 1));
  const dirty = !!draft && !!pitch && JSON.stringify(draft) !== JSON.stringify(pitch.slides);

  useEffect(() => {
    if (!dirty) return;
    const onUnload = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [dirty]);

  const go = useCallback(
    (n: number) => {
      setIdx(Math.max(0, Math.min(total - 1, n)));
      setActiveBullet(null);
      setActiveCite(null);
    },
    [total],
  );
  const focus = useCallback((slideNo: number, bullet: number | null) => {
    setIdx(Math.max(0, slideNo - 1));
    setActiveBullet(bullet);
    setActiveCite(null);
  }, []);
  const startEdit = useCallback(
    (slideNo?: number, bullet?: number | null) => {
      if (!pitch) return;
      setDraft((d) => d ?? (JSON.parse(JSON.stringify(pitch.slides)) as Slide[]));
      setPreview(null);
      if (slideNo) focus(slideNo, bullet ?? null);
    },
    [pitch, focus],
  );
  const discard = useCallback(() => {
    setDraft(null);
    setPreview(null);
  }, []);
  const patchSlide = useCallback((i: number, patch: Partial<Slide>) => setDraft((d) => (d ? d.map((s, k) => (k === i ? { ...s, ...patch } : s)) : d)), []);
  const replaceSlide = useCallback(
    (slide: Slide) => {
      if (!pitch) return;
      setDraft((d) => {
        const base = d ?? (JSON.parse(JSON.stringify(pitch.slides)) as Slide[]);
        return base.map((item) => (item.slide_number === slide.slide_number ? slide : item));
      });
      setPreview(null);
      focus(slide.slide_number, null);
    },
    [pitch, focus],
  );
  const removeBullet = useCallback(
    (slideNo: number, bi: number) => {
      const base = draft ?? pitch?.slides;
      if (!base) return;
      const next = base.map((s, k) => (k === slideNo - 1 ? { ...s, bullets: s.bullets.filter((_, j) => j !== bi) } : s));
      setDraft(JSON.parse(JSON.stringify(next)) as Slide[]);
      setPreview(null);
      focus(slideNo, null);
    },
    [draft, pitch, focus],
  );

  return useMemo(
    () => ({ idx, go, slides, total, draft, dirty, preview, setPreview, activeBullet, setActiveBullet, activeCite, setActiveCite, passport, setPassport, focus, startEdit, discard, patchSlide, replaceSlide, removeBullet }),
    [idx, go, slides, total, draft, dirty, preview, activeBullet, activeCite, passport, focus, startEdit, discard, patchSlide, replaceSlide, removeBullet],
  );
}

export type Deck = ReturnType<typeof useDeck>;
