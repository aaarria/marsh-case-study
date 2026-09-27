"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { StatusPill } from "@/components/status-badge";
import { SourceQuote } from "@/components/source-ref";
import { api, ApiError } from "@/lib/api";
import type { ScenarioCell, ScenarioResult } from "@/lib/types";
import type { Tone } from "@/components/callout";

const EXAMPLES = [
  "The client wants strong maternity coverage.",
  "The workforce includes employees with pre-existing diabetes.",
  "The client wants protection against non-medical hospitalization expenses.",
  "The client wants protection for international treatment.",
  "The client wants high flexibility if the base sum insured is exhausted.",
];

const TONE: Record<string, Tone> = {
  COVERED: "ok",
  CONDITIONAL: "warn",
  PARTIAL: "warn",
  ADD_ON: "info",
  EXCLUDED: "danger",
  NOT_ESTABLISHED: "neutral",
  REVIEW_REQUIRED: "warn",
};

function CellDetail({ cell }: { cell: ScenarioCell }) {
  const [level, setLevel] = useState<"summary" | "evidence" | "technical">("summary");
  return (
    <div className="rounded-md border border-hairline p-2">
      <div className="flex items-start justify-between gap-2">
        <div className="text-xs font-medium text-ink">{cell.policy_name}</div>
        <StatusPill tone={TONE[cell.state] || "neutral"} size="xs" title={cell.state}>{cell.label}</StatusPill>
      </div>
      <p className="mt-1 text-xs text-body">{cell.explanation || "No explanation was recorded for this cell."}</p>
      <div className="mt-1 flex gap-2 text-2xs">
        <button type="button" className="focus-ring text-ink underline-offset-2 hover:underline" onClick={() => setLevel("evidence")}>Evidence</button>
        <button type="button" className="focus-ring text-ink underline-offset-2 hover:underline" onClick={() => setLevel("technical")}>Technical detail</button>
      </div>
      {level !== "summary" && (
        <div className="mt-2 space-y-1">
          {cell.quote ? <SourceQuote>{cell.quote}</SourceQuote> : <p className="text-xs text-muted-foreground">No brochure quote was stored. That leaves the point not established.</p>}
          {cell.conditions.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-body">
              {cell.conditions.map((item) => <li key={item}>{item}</li>)}
            </ul>
          )}
          {level === "technical" && (
            <p className="font-mono text-2xs text-quiet">
              {cell.state}
              {cell.page ? ` · p.${cell.page}` : ""}
              {cell.section ? ` · ${cell.section}` : ""}
              {cell.chunk_id ? ` · ${cell.chunk_id}` : ""}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** Advisor scenario check. It reads stored facts and does not choose a policy. */
export function ScenarioPanel({ runId }: { runId: string }) {
  const [text, setText] = useState(EXAMPLES[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScenarioResult | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.scenario(runId, text));
    } catch (e) {
      setResult(null);
      setError(e instanceof ApiError ? e.message : "The scenario could not be evaluated.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 space-y-2 border-t border-hairline pt-3">
      <div>
        <div className="text-sm font-medium text-ink">COVERAGE SCENARIO ANALYSIS</div>
        <p className="text-xs text-muted-foreground">Check how each policy addresses one client situation. The result is evidence for you. It does not change the recommendation and it does not promise a claim payment.</p>
      </div>
      <Textarea rows={2} maxLength={500} value={text} onChange={(e) => setText(e.target.value)} aria-label="Client scenario" />
      <div className="flex flex-wrap gap-1.5">
        {EXAMPLES.map((example) => (
          <button key={example} type="button" className="focus-ring rounded-md border border-hairline px-1.5 py-0.5 text-2xs text-body hover:text-ink" onClick={() => setText(example)}>
            {example}
          </button>
        ))}
      </div>
      <Button size="sm" type="button" loading={busy} disabled={busy || text.trim().length < 12} onClick={run}>Evaluate all policies</Button>
      {error && <p className="text-xs text-body">{error}</p>}
      {result && (
        <div className="space-y-2">
          <p className="text-xs text-body">{result.message}</p>
          {result.rows.map((row) => (
            <section key={row.feature} className="space-y-1">
              <div className="text-xs font-medium text-ink">{row.label}</div>
              <div className="grid gap-1.5 sm:grid-cols-2">
                {row.cells.map((cell) => <CellDetail key={`${row.feature}-${cell.policy_id}`} cell={cell} />)}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
