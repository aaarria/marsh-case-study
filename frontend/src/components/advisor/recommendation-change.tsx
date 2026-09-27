"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import type { RecommendationChangeResult } from "@/lib/types";

/** Recalculate fit from a client priority. The named policy is applied only when the engine supports it. */
export function RecommendationChange({ runId, onChanged }: { runId: string; onChanged: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RecommendationChangeResult | null>(null);

  const send = async (apply: boolean, override: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const next = await api.recommendationChange(runId, { instruction, reviewer: reviewer || undefined, override, apply });
      setResult(next);
      if (next.applied) await onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The recommendation was not changed.");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className="focus-ring mt-2 text-xs text-ink underline-offset-2 hover:underline" onClick={() => setOpen(true)}>
        Recalculate from a client priority
      </button>
    );
  }

  return (
    <div className="mt-2 space-y-2 rounded-md border border-hairline p-2">
      <div className="text-xs font-medium text-ink">Recalculate the recommendation</div>
      <p className="text-xs text-muted-foreground">Name the client priority. The fit engine scores every policy again. Asking for a named policy does not set it unless the engine supports it, or you record a named override.</p>
      <Textarea rows={2} maxLength={500} value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="The client wants strong maternity coverage." aria-label="Recommendation change" />
      <Input value={reviewer} maxLength={120} onChange={(e) => setReviewer(e.target.value)} placeholder="Your name, required for an override" aria-label="Override reviewer" />
      <div className="flex flex-wrap gap-2">
        <Button size="sm" type="button" loading={busy} disabled={busy || instruction.trim().length < 12} onClick={() => send(true, false)}>Recalculate</Button>
        <Button size="sm" type="button" variant="outline" disabled={busy || !reviewer.trim() || instruction.trim().length < 12} onClick={() => send(true, true)}>Record named override</Button>
      </div>
      {error && <p className="text-xs text-body">{error}</p>}
      {result && (
        <div className="space-y-1 text-xs">
          <p className="text-body">{result.message}</p>
          {result.interpreted_change && result.interpreted_change.length > 0 && (
            <p className="text-muted-foreground">Interpreted change: {result.interpreted_change.join(" ")}</p>
          )}
          {result.old_weights && result.new_weights && (
            <p className="text-muted-foreground">
              Weights {result.old_weights.map((row) => `${row.feature} ${Math.round(row.weight * 100)}%`).join(", ") || "none"}
              {" → "}
              {result.new_weights.map((row) => `${row.feature} ${Math.round(row.weight * 100)}%`).join(", ")}
            </p>
          )}
          {result.scores && result.scores.length > 0 && (
            <ul className="space-y-0.5 text-muted-foreground">
              {result.scores.map((row) => (
                <li key={row.policy_id}>{row.policy_name || row.policy_id}: {row.fit_score ?? "—"} · {row.decision_state || "not scored"}</li>
              ))}
            </ul>
          )}
          {result.gaps && result.gaps.length > 0 && <p className="text-muted-foreground">Gap: {result.gaps.join(", ")}</p>}
          {result.applied && <p className="text-ink">Applied. Regenerate the pitch and wait for the new audit before approving.</p>}
        </div>
      )}
    </div>
  );
}
