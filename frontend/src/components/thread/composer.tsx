"use client";

import { useState } from "react";
import { ArrowUp } from "lucide-react";
import { Matrix } from "@/components/matrix";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { api, ApiError } from "@/lib/api";
import { shortName } from "@/lib/format";
import type { Answer, RunState } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The thread's input, always at the bottom like an agent composer. What Enter does depends on what
 * the run is waiting for: context → recorded as advisor notes and the profile re-runs; a close call →
 * the named policy is pitched; review → the text goes back to the writer as feedback and the draft is
 * regenerated and re-audited. Approval stays a deliberate button, never a typed word.
 */
export function ThreadComposer({ state, refresh, policyName }: { state: RunState; refresh: () => Promise<void>; policyName: (id?: string | null) => string }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = state.question;
  const status = state.run.status;
  const company = state.run.company_name;

  let placeholder = "";
  let hint: React.ReactNode = null;
  let build: ((t: string) => Answer | null) | null = null;
  if (status === "running") placeholder = "Working. You can answer when it pauses";
  else if (status === "failed") placeholder = "Paused on an error. Retry from the message above";
  else if (status === "approved" || status === "rejected") placeholder = "This pitch is closed. Start a new one from the rail.";
  else if (q?.question === "context") {
    placeholder = `What do you know about ${company}?`;
    hint = "Industry, headcount, sites, pain points… recorded as a verified advisor input; the profile re-runs with it.";
    build = (t) => ({ action: "add_context", advisor_notes: t });
  } else if (q?.question === "close_call") {
    const names = q.options.filter((o) => o.id !== "keep").map((o) => shortName(o.label));
    placeholder = `Type a policy to pitch (${names.join(" / ")}) or "keep" to let the score decide`;
    hint = "Or pick one on the card above.";
    build = (t) => {
      const s = t.trim().toLowerCase();
      if (s === "keep" || s.startsWith("let the score")) return { action: "keep" };
      const hit = q.options.find((o) => o.id !== "keep" && (o.id.toLowerCase() === s || o.label.toLowerCase().includes(s) || shortName(o.label).toLowerCase().includes(s) || shortName(policyName(o.id)).toLowerCase().includes(s)));
      return hit ? { action: hit.id } : null;
    };
  } else if (q?.question === "review") {
    placeholder = "Edit a slide in the deck. Approval stays on the card above.";
    hint = "Select a bullet on the slide and describe the edit. Verified facts, scores, and citations stay locked.";
  } else placeholder = "Waiting…";

  const enabled = !!build && !busy;
  const working = status === "running";
  const send = async () => {
    const t = text.trim();
    if (!build || !t || busy) return;
    const body = build(t);
    if (!body) return setError("No policy in scope matches that name.");
    setBusy(true);
    setError(null);
    try {
      await api.answer(state.run.run_id, body);
      setText("");
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shrink-0 border-t border-hairline bg-marsh-cream px-4 pb-4 pt-3">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
        className={cn("panel flex items-end gap-2 p-2 transition-colors", enabled ? "focus-within:border-hairline-bright" : "opacity-70")}
      >
        <textarea
          rows={Math.min(5, Math.max(1, text.split("\n").length))}
          value={text}
          disabled={!enabled}
          maxLength={2000}
          onChange={(e) => {
            setText(e.target.value);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={placeholder}
          aria-label="Message the run"
          className="thin-scroll min-h-8 flex-1 resize-none bg-transparent px-1.5 py-1.5 text-sm leading-6 text-ink outline-none placeholder:truncate placeholder:text-quiet disabled:cursor-not-allowed"
        />
        {/* Sending: spinner (request in flight). Agent working: the scan matrix, like the thread's working row and the status bar. */}
        <Button type="submit" size="sm" disabled={!enabled || !text.trim()} loading={busy} aria-busy={working || undefined} aria-label={working ? "Working" : "Send"} className={cn(working && "disabled:opacity-100")}>
          {working ? <Matrix variant="scan" title="Working" /> : <>Send <ArrowUp className="size-4" /></>}
        </Button>
      </form>
      <div className="mt-1.5 flex items-center justify-between gap-3 px-1 text-2xs text-quiet">
        <span className="truncate">{error ? <span className="tone-danger tint-text">{error}</span> : hint}</span>
        {enabled && (
          <span className="flex shrink-0 items-center gap-1">
            <Kbd>↵</Kbd> send · <Kbd>⇧↵</Kbd> newline
          </span>
        )}
      </div>
    </div>
  );
}
