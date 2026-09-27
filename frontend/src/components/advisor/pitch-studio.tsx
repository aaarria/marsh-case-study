"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { GateBadge } from "@/components/status-badge";
import { api, ApiError } from "@/lib/api";
import type { Deck } from "@/components/deck/use-deck";
import type { Slide, StudioProposal } from "@/lib/types";

const PROMPTS = [
  "Make this more executive-friendly.",
  "Reduce the amount of text.",
  "Turn this into a process diagram.",
  "Make the comparison easier to scan.",
  "Use a timeline.",
  "Create a stronger visual hierarchy.",
  "Convert this content into a 3-step flow.",
  "Make this suitable for a senior HR audience.",
];

/** Structured presentation changes. Facts stay on the slide model until the advisor accepts them. */
export function PitchStudio({ runId, slide, canAccept, deck }: { runId: string; slide: Slide; canAccept: boolean; deck: Deck }) {
  const [instruction, setInstruction] = useState(PROMPTS[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<StudioProposal | null>(null);
  const [placed, setPlaced] = useState<{ subtitle: string | null; message: string } | null>(null);
  const note = placed && deck.dirty && slide.subtitle === placed.subtitle ? placed.message : null;

  const propose = async () => {
    setBusy(true);
    setError(null);
    setPlaced(null);
    setProposal(null);
    try {
      setProposal(await api.pitchStudio(runId, { slide_number: slide.slide_number, instruction }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The proposal was refused.");
    } finally {
      setBusy(false);
    }
  };

  const accept = () => {
    if (!proposal || proposal.audit_blocks_accept) return;
    deck.replaceSlide(proposal.slide);
    setProposal(null);
    setPlaced({
      subtitle: proposal.slide.subtitle ?? null,
      message: "Placed on the deck as an unsaved edit. Save and re-audit from the review card before approval. Policy facts were not rewritten.",
    });
  };

  return (
    <div className="panel space-y-2 p-4">
      <div>
        <div className="text-sm font-medium text-ink">ADVISORY PITCH STUDIO</div>
        <p className="text-xs text-muted-foreground">Ask for a presentation change on slide {slide.slide_number}. Policy facts, numbers, evidence, and the recommendation stay locked. Nothing is saved until you accept it and the review card re-audits the deck.</p>
      </div>
      <Textarea rows={2} maxLength={400} value={instruction} onChange={(e) => setInstruction(e.target.value)} aria-label="Presentation instruction" />
      <div className="flex flex-wrap gap-1.5">
        {PROMPTS.map((prompt) => (
          <button key={prompt} type="button" className="focus-ring rounded-md border border-hairline px-1.5 py-0.5 text-2xs text-body hover:text-ink" onClick={() => setInstruction(prompt)}>
            {prompt}
          </button>
        ))}
      </div>
      <Button size="sm" type="button" loading={busy} disabled={busy || instruction.trim().length < 8} onClick={propose}>Preview change</Button>
      {error && <p className="text-xs text-body">{error}</p>}
      {proposal && (
        <div className="space-y-2 rounded-md border border-hairline p-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-ink">Proposed · {proposal.intent || "simplify"}</span>
            {proposal.audit?.gate && <GateBadge gate={proposal.audit.gate} size="xs" />}
          </div>
          <p className="text-body">{proposal.message}</p>
          <p className="text-muted-foreground">Layout: {slide.layout} → {proposal.slide.layout}. {proposal.wording ? "Wording was rewritten." : "Wording was not rewritten."}</p>
          {(proposal.changes || []).length > 0 && (
            <ul className="space-y-1">
              {proposal.changes?.map((change) => (
                <li key={`${change.index}-${change.after}`} className="rounded-md border border-hairline p-1.5">
                  <div className="text-2xs text-quiet">Changed wording</div>
                  <div className="text-muted-foreground line-through">{change.before}</div>
                  <div className="text-ink">{change.after}</div>
                </li>
              ))}
            </ul>
          )}
          <div className="text-muted-foreground">
            Unchanged locked facts: numbers, policy names, recommendation {proposal.locks?.RECOMMENDATION_LOCK || "identity"}, and evidence {proposal.locks?.EVIDENCE_LOCK?.length ? proposal.locks.EVIDENCE_LOCK.join(", ") : "references"}.
          </div>
          {proposal.audit && (
            <p className="text-muted-foreground">Audit of this proposal: {proposal.audit.gate || "not audited"} · {proposal.audit.supported} supported · {proposal.audit.contradicted} contradicted · {proposal.audit.not_found} not found.</p>
          )}
          <div className="flex gap-2">
            <Button size="sm" type="button" disabled={!canAccept || proposal.audit_blocks_accept || proposal.acceptable === false} title={proposal.audit_blocks_accept ? "The audit failed, so this proposal cannot be applied" : !canAccept ? "Apply during review, then save and re-audit" : undefined} onClick={accept}>
              Apply
            </Button>
            <Button size="sm" type="button" variant="ghost" onClick={() => setProposal(null)}>Discard</Button>
          </div>
          {proposal.audit_blocks_accept && <p className="text-body">The audit failed. The deck was not changed.</p>}
        </div>
      )}
      {note && <p className="text-xs text-ink">{note}</p>}
    </div>
  );
}
