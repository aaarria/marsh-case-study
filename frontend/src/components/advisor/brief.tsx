"use client";

import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { StatusPill } from "@/components/status-badge";
import { SourceQuote } from "@/components/source-ref";
import { api, ApiError } from "@/lib/api";
import type { AdvisorView, EvidenceLookup } from "@/lib/types";
import type { Tone } from "@/components/callout";

const STATE_TONE: Record<string, Tone> = {
  COVERED: "ok",
  CONDITIONAL: "warn",
  PARTIAL: "warn",
  ADD_ON: "info",
  EXCLUDED: "danger",
  NOT_ESTABLISHED: "neutral",
  REVIEW_REQUIRED: "warn",
};

function Mark({ on, label }: { on: boolean; label: string }) {
  return (
    <li className="flex items-center gap-2 text-xs">
      <span className={on ? "text-ink" : "text-quiet"}>{on ? "✓" : "–"}</span>
      <span className="text-body">{label}</span>
    </li>
  );
}

export function EvidenceDrawer({ runId, policyId, feature, label, onClose }: { runId: string; policyId: string; feature: string; label: string; onClose: () => void }) {
  const [item, setItem] = useState<EvidenceLookup | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.evidence(runId, policyId, feature).then((found) => {
      if (!cancelled) setItem(found);
    }).catch((e) => {
      if (!cancelled) setError(e instanceof ApiError ? e.message : "Evidence could not be opened.");
    });
    return () => {
      cancelled = true;
    };
  }, [runId, policyId, feature]);
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="thin-scroll w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader className="pr-10">
          <SheetTitle>{item?.product || item?.policy_id || label}</SheetTitle>
          <SheetDescription>
            {item?.insurer ? `${item.insurer}` : "Brochure evidence"}
            {item?.page ? ` · p.${item.page}` : ""}
            {item?.section ? ` · ${item.section}` : ""}
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-3 px-4 pb-6">
          {error && <p className="text-sm text-body">{error}</p>}
          {!error && !item && <p className="text-sm text-muted-foreground">Opening the cited passage…</p>}
          {item && (
            <>
              <StatusPill tone={item.status_label.startsWith("Excluded") ? "danger" : item.status_label.startsWith("Not established") ? "neutral" : "ok"} size="xs">
                {item.status_label}
              </StatusPill>
              <div className="text-xs text-muted-foreground">{item.feature_label}</div>
              {item.source_document && <div className="text-xs text-muted-foreground">{item.source_document}</div>}
              {item.quote ? <SourceQuote>{item.quote}</SourceQuote> : <p className="text-sm text-body">Not established from the available evidence. That is not the same as an exclusion.</p>}
              {item.conditions.length > 0 && (
                <ul className="list-disc space-y-1 pl-4 text-xs text-body">
                  {item.conditions.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              )}
              {item.quote && item.page && (
                <Button size="sm" variant="outline" render={<a href={api.documentUrl(item.policy_id, item.page)} target="_blank" rel="noreferrer" />}>
                  Open source
                </Button>
              )}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

export function RecommendationBrief({ view, runId }: { view: AdvisorView; runId: string }) {
  const rec = view.recommendation;
  const [maths, setMaths] = useState(false);
  const [evidence, setEvidence] = useState<{ policyId: string; feature: string; label: string } | null>(null);
  if (!rec) return null;
  return (
    <div className="space-y-2">
      {rec.automatic ? (
        <>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Recommended</p>
          <p className="text-sm text-ink">{rec.policy_name}</p>
          <p className="text-xs text-body">{rec.wording}</p>
        </>
      ) : (
        <p className="text-sm text-body">{rec.wording}</p>
      )}
      <StatusPill tone={rec.decision_label === "ELIGIBLE" || rec.decision_label === "ADVISOR OVERRIDE" ? "ok" : rec.decision_label === "INELIGIBLE" ? "danger" : "warn"} size="xs">
        {rec.decision_label}
      </StatusPill>
      {rec.changed_after_check && <p className="text-xs text-muted-foreground">The recommendation changed because validated evidence was added, then the same calculation was run again.</p>}
      {rec.drivers.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs font-medium text-ink">Recommended because</div>
          <ol className="list-decimal space-y-0.5 pl-4 text-xs">
            {rec.drivers.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ol>
        </div>
      )}
      {rec.automatic && (
        <p className="font-mono text-2xs tabular-nums text-quiet">
          Fit {rec.fit_score}/100 · decision support only
          {rec.evidence_completeness != null ? ` · evidence ${Math.round(rec.evidence_completeness * 100)}%` : ""}
        </p>
      )}
      {rec.gaps.length > 0 && <p className="text-xs text-muted-foreground">{rec.gaps.slice(0, 3).join(" · ")}</p>}
      {(rec.requirements?.length || 0) > 0 && (
        <details className="rounded-md border border-hairline p-2" open>
          <summary className="cursor-pointer text-xs text-ink">How the selected priorities affect the comparison</summary>
          <ul className="mt-2 space-y-2">
            {rec.requirements?.map((row) => (
              <li key={row.feature} className="text-xs">
                <div className="font-medium text-ink">{row.concept || row.label}</div>
                {row.concept && row.label && row.label !== row.concept && <div className="text-muted-foreground">{row.label}</div>}
                <div className="text-muted-foreground">Mapped requirement: {row.concept || row.label}. Weight {Math.round((row.weight || 0) * 100)}%.</div>
                <ul className="mt-1 space-y-0.5 text-body">
                  {row.results.map((cell) => (
                    <li key={cell.policy_id}>{cell.policy_name}: {cell.label}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </details>
      )}
      {rec.automatic && (
        <div className="space-y-2 text-xs">
          <div className="font-medium text-ink">Alternatives</div>
          {(rec.alternatives?.length || 0) === 0 ? (
            <p className="text-muted-foreground">No additional policy has sufficient evidence for a reliable comparison.</p>
          ) : (
            rec.alternatives?.map((alt, index) => (
              <div key={alt.policy_id || index} className="rounded-md border border-hairline p-2">
                <div className="font-medium text-ink">{index === 0 ? "Next best alternative" : "Third option"}: {alt.policy_name}</div>
                <p className="text-quiet">Fit {alt.fit_score}/100 · decision support only</p>
                {alt.strong_matches.length > 0 && <p>Satisfies: {alt.strong_matches.join("; ")}</p>}
                {alt.trade_offs.length > 0 && <p>Trade-off: {alt.trade_offs.join("; ")}</p>}
                {alt.evidence.length > 0 && <p className="text-muted-foreground">Evidence: {alt.evidence.slice(0, 2).join(" ")}</p>}
              </div>
            ))
          )}
        </div>
      )}
      {(view.why?.length || 0) > 0 && (
        <ul className="space-y-2">
          {view.why?.map((row) => (
            <li key={`${row.feature}-${row.requirement}`} className="rounded-md border border-hairline p-2 text-xs">
              <div className="font-medium text-ink">{row.requirement}</div>
              <div className="text-muted-foreground">Requirement: {row.priority.replace("_", " ").toLowerCase()}</div>
              <div>Result: {row.result}</div>
              <div className="text-muted-foreground">Evidence: {row.policy_name || "Brochure"}{row.page ? `, p.${row.page}` : ""}</div>
              {row.impact && <div>{row.impact}</div>}
              {row.feature && rec.policy_id && (
                <Button type="button" size="xs" variant="outline" className="mt-2" onClick={() => setEvidence({ policyId: rec.policy_id, feature: row.feature || "", label: row.requirement })}>
                  View evidence
                </Button>
              )}
            </li>
          ))}
          <Button type="button" size="xs" variant="outline" onClick={() => setMaths((v) => !v)}>
            {maths ? "Hide score detail" : "Score detail"}
          </Button>
          {maths && (
            <ul className="space-y-1 text-2xs text-quiet">
              {view.why?.map((row) => (
                <li key={`w-${row.feature}`}>
                  {row.requirement}: contribution {row.contribution ?? "not scored"}, weight {row.weight ?? "not scored"}
                  {row.chunk_id ? ` · ${row.chunk_id}` : ""}
                </li>
              ))}
            </ul>
          )}
        </ul>
      )}
      {evidence && <EvidenceDrawer key={`${evidence.policyId}:${evidence.feature}`} runId={runId} policyId={evidence.policyId} feature={evidence.feature} label={evidence.label} onClose={() => setEvidence(null)} />}
    </div>
  );
}

export function ComparisonBrief({ view, runId }: { view: AdvisorView; runId: string }) {
  const comparison = view.comparison;
  const [evidence, setEvidence] = useState<{ policyId: string; feature: string; label: string } | null>(null);
  if (!comparison || comparison.rows.length === 0) return <p className="text-xs text-muted-foreground">The comparison is not ready. Missing cells stay not established.</p>;
  return (
    <div className="space-y-2">
      <div>
        <div className="text-sm font-medium text-ink">Policy comparison</div>
        <p className="text-xs text-muted-foreground">
          {comparison.baseline
            ? "No specific client priority was selected. The comparison therefore uses the standard baseline coverage criteria."
            : "Each row is a requirement that affects this comparison. The label is the evidence state. This view does not calculate fit."}
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[28rem] border-collapse text-left text-xs">
          <thead>
            <tr className="border-b border-hairline text-muted-foreground">
              <th className="py-2.5 pr-3 font-medium">Requirement</th>
              {comparison.policies.map((policy) => (
                <th key={policy.policy_id} className="px-2 py-2.5 font-medium">{policy.policy_name || policy.policy_id}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {comparison.rows.map((row) => (
              <tr key={row.feature} className="border-b border-hairline align-top">
                <th className="py-3 pr-3 font-medium text-ink">{row.label}</th>
                {row.cells.map((cell) => (
                  <td key={cell.policy_id} className="px-2 py-3">
                    <button type="button" className="focus-ring rounded-sm text-left" title={`${cell.policy_name || cell.policy_id}: ${cell.status_label}. Open the brochure evidence.`} onClick={() => setEvidence({ policyId: cell.policy_id, feature: row.feature, label: row.label })}>
                      <StatusPill tone={STATE_TONE[cell.state || ""] || "neutral"} size="xs">{cell.status_label}</StatusPill>
                      {(cell.page || cell.section) && (
                        <div className="mt-0.5 text-2xs text-quiet">
                          {cell.page ? `p. ${cell.page}` : ""}
                          {cell.section ? `${cell.page ? ", " : ""}${cell.section}` : ""}
                        </div>
                      )}
                    </button>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-2xs text-quiet">Not established means the brochure evidence does not settle the point. It is not an exclusion. Select a cell to read the quote, page, and evidence reference.</p>
      {evidence && <EvidenceDrawer key={`${evidence.policyId}:${evidence.feature}`} runId={runId} policyId={evidence.policyId} feature={evidence.feature} label={evidence.label} onClose={() => setEvidence(null)} />}
    </div>
  );
}

export function PolicyCheckBrief({ view, runId }: { view: AdvisorView; runId: string }) {
  const check = view.policy_check;
  const [open, setOpen] = useState(false);
  const [evidence, setEvidence] = useState<{ policyId: string; feature: string; label: string } | null>(null);
  if (!check) return null;
  const unavailable = check.status !== "COMPLETED";
  return (
    <div className="space-y-2">
      <div>
        <div className="text-sm font-medium text-ink">Policy Check</div>
        <p className="text-xs text-muted-foreground">Stress-test your recommendation before it reaches the client.</p>
      </div>
      <ul className="space-y-0.5">
        <Mark on={check.checked.challenge} label={unavailable ? "Challenge unavailable" : "Challenge checked"} />
        <Mark on={check.checked.scenarios} label={unavailable ? "Scenarios unavailable" : "Scenario tested"} />
        <Mark on={check.checked.gaps} label={unavailable ? "Gap analysis unavailable" : "Gap analysis completed"} />
      </ul>
      <StatusPill tone={check.stability === "STABLE" ? "ok" : check.stability === "SENSITIVE" ? "warn" : "neutral"} size="xs">
        Stability: {check.stability === "UNAVAILABLE" ? "Not stress-tested" : check.stability[0] + check.stability.slice(1).toLowerCase()}
      </StatusPill>
      <p className="text-xs text-body">{check.note}</p>
      <Button type="button" size="sm" variant="outline" onClick={() => setOpen((v) => !v)}>
        <ShieldCheck className="size-3.5" /> {open ? "Hide Policy Check" : "Inspect Policy Check"}
      </Button>
      {open && (
        <div className="space-y-3">
          <section className="rounded-md border border-hairline p-2">
            <div className="text-xs font-medium text-ink">Challenge</div>
            <p className="text-2xs text-muted-foreground">Can another policy make a stronger evidence-backed case?</p>
            {check.challenge_found && check.challenge ? (
              <div className="mt-1 space-y-0.5 text-xs">
                <div>{check.challenge.policy_name} on {check.challenge.requirement}</div>
                <div className="text-muted-foreground">{check.challenge.explanation}</div>
                {check.challenge.evidence_id && <div className="text-quiet">Evidence {check.challenge.evidence_id}</div>}
                {check.challenge.policy_id && check.challenge.feature && (
                  <Button type="button" size="xs" variant="outline" className="mt-2" onClick={() => setEvidence({ policyId: check.challenge!.policy_id || "", feature: check.challenge!.feature || "", label: check.challenge!.requirement })}>
                    View evidence
                  </Button>
                )}
              </div>
            ) : (
              <p className="mt-1 text-xs text-body">{unavailable ? "Policy Check did not run, so no challenge was recorded." : "No evidence-backed case that another policy fits better on the current facts."}</p>
            )}
          </section>
          <section className="rounded-md border border-hairline p-2">
            <div className="text-xs font-medium text-ink">Scenarios</div>
            <p className="text-2xs text-muted-foreground">How does each policy perform against realistic client scenarios?</p>
            <ul className="mt-1 space-y-2">
              {check.scenarios.map((scenario) => (
                <li key={scenario.scenario} className="text-xs">
                  <div className="text-ink">{scenario.scenario}</div>
                  <div className="text-muted-foreground">{scenario.requirement}</div>
                  <ul className="mt-0.5">
                    {scenario.outcomes.map((outcome) => (
                      <li key={outcome.policy_id} className="flex justify-between gap-2">
                        <span>{outcome.policy_name}</span>
                        <span className="text-muted-foreground">{outcome.result}</span>
                      </li>
                    ))}
                  </ul>
                  {scenario.limitation && <div className="text-muted-foreground">{scenario.limitation}</div>}
                  {scenario.feature && scenario.outcomes[0]?.policy_id && (
                    <Button type="button" size="xs" variant="outline" className="mt-2" onClick={() => setEvidence({ policyId: scenario.outcomes[0].policy_id || "", feature: scenario.feature || "", label: scenario.requirement })}>
                      View evidence
                    </Button>
                  )}
                </li>
              ))}
              {check.scenarios.length === 0 && <li className="text-xs text-muted-foreground">No client scenario was available to test.</li>}
            </ul>
          </section>
          <section className="rounded-md border border-hairline p-2">
            <div className="text-xs font-medium text-ink">Gaps</div>
            <p className="text-2xs text-muted-foreground">What important exposure remains unresolved?</p>
            <ul className="mt-1 space-y-1 text-xs">
              {check.gaps.map((gap, i) => (
                <li key={`${gap.kind}-${gap.feature}-${i}`}>
                  <span className="text-ink">{gap.feature}</span>
                  {gap.policy_name ? ` · ${gap.policy_name}` : ""}: {gap.meaning}
                </li>
              ))}
              {check.gaps.length === 0 && <li className="text-muted-foreground">No gap was recorded.</li>}
            </ul>
          </section>
        </div>
      )}
      {evidence && <EvidenceDrawer key={`${evidence.policyId}:${evidence.feature}`} runId={runId} policyId={evidence.policyId} feature={evidence.feature} label={evidence.label} onClose={() => setEvidence(null)} />}
    </div>
  );
}
