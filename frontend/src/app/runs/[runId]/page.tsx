"use client";

import { use, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { DeckPane } from "@/components/deck/deck-pane";
import { useDeck } from "@/components/deck/use-deck";
import { Thread } from "@/components/thread/thread";
import { GateBadge, RunStatusBadge } from "@/components/status-badge";
import { ErrorBlock, LoadingBlock } from "@/components/states";
import { api } from "@/lib/api";
import { shortName } from "@/lib/format";
import { useRun } from "@/lib/use-run";
import type { PolicyDocument } from "@/lib/types";

/** One pitch = one thread (left) and its deck (right). Questions arrive in the thread; the deck is always in view. */
export default function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = use(params);
  const { state, error, loading, refresh } = useRun(runId);
  const [policies, setPolicies] = useState<PolicyDocument[]>([]);
  useEffect(() => {
    api.policies().then((p) => setPolicies(p.policies)).catch(() => {});
  }, []);
  const policyName = useMemo(() => (id?: string | null) => policies.find((p) => p.policy_id === id)?.policy_name || id || "", [policies]);
  const deck = useDeck(state?.values.pitch);

  const rec = state?.values.recommendation;
  const gate = state?.values.audit?.summary.gate ?? state?.run.audit_gate;

  return (
    <AppShell runId={runId} refreshKey={state?.run.status}>
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-hairline px-5">
        <h1 className="truncate text-sm font-medium text-ink">{state?.run.company_name || "Pitch"}</h1>
        {state && <RunStatusBadge status={state.run.status} errorKind={state.run.error_kind} size="xs" />}
        <GateBadge gate={gate} size="xs" />
        {rec && (
          <span className="hidden truncate text-xs text-muted-foreground lg:inline">
            Recommends <span className="text-ink">{shortName(policyName(rec.recommended_policy_id))}</span> · fit {rec.fit_score}/100
          </span>
        )}
      </header>
      <div className="flex min-h-0 flex-1">
        <section className="w-[28rem] shrink-0 border-r border-hairline xl:w-[32rem]" aria-label="Thread">
          {error && !state ? <ErrorBlock message={error} title="Cannot load run" className="m-5" /> : loading && !state ? <div className="p-5"><LoadingBlock label="Loading run…" /></div> : state ? <Thread state={state} deck={deck} policyName={policyName} refresh={refresh} /> : null}
        </section>
        <section className="min-w-0 flex-1 bg-canvas" aria-label="Deck">
          {state && <DeckPane state={state} deck={deck} />}
        </section>
      </div>
    </AppShell>
  );
}
