"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Columns3, Download, FileSearch, Gauge, Globe, Hourglass, ListChecks, MessageCircleQuestion, Presentation, RotateCcw, ShieldCheck, TimerReset, Unplug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/callout";
import { GateBadge, KindBadge, StatusPill } from "@/components/status-badge";
import { AssistantMessage, ChipRow, ToolRow, UserMessage, WorkingRow } from "@/components/thread/message";
import { ThreadComposer } from "@/components/thread/composer";
import { Stream, StreamScope } from "@/components/stream";
import { CloseCallQuestion, ContextQuestion, ReviewQuestion } from "@/components/thread/question-card";
import { ComparisonBrief, PolicyCheckBrief, RecommendationBrief } from "@/components/advisor/brief";
import { ScenarioPanel } from "@/components/advisor/scenario-panel";
import { RecommendationChange } from "@/components/advisor/recommendation-change";
import type { Deck } from "@/components/deck/use-deck";
import { api } from "@/lib/api";
import { fmtRelative, shortName } from "@/lib/format";
import { intakeChips, storedCompanyName } from "@/lib/intake";
import { NODE_EXPLAIN, NODE_LABELS } from "@/lib/pipeline";
import { buildThread, type ThreadItem } from "@/lib/thread";
import type { RunState, RunSummary } from "@/lib/types";

const DOWNLOADS: { kind: string; label: string }[] = [
  { kind: "pitch_pptx", label: "Client deck (.pptx)" },
  { kind: "audit_md", label: "Audit report (.md)" },
  { kind: "audit_json", label: "Audit data (.json)" },
];
/** A run is considered stalled when it says `running` but nothing has happened for this long. */
const STALL_MS = 8 * 60 * 1000;

function ts(iso?: string | null) {
  return iso ? fmtRelative(iso) : "";
}
function toMs(iso: string) {
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).getTime();
}
/** "12 s" / "1 m 05 s" since a step started, for the live row. */
function elapsed(startIso: string, nowMs: number): string {
  const s = Math.max(0, Math.round((nowMs - toMs(startIso)) / 1000));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} m ${String(s % 60).padStart(2, "0")} s`;
}
function formatWait(seconds: number): string {
  if (seconds <= 0) return "now";
  if (seconds < 90) return `~${Math.ceil(seconds)} s`;
  if (seconds < 3600) return `~${Math.ceil(seconds / 60)} min`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return `~${h} h${m ? ` ${m} min` : ""}`;
}

/** What each completed step says. Uses the latest values; older repeats of a step are summarised from history. */
function StepMessage({ item, state, policyName, deck, refresh }: { item: Extract<ThreadItem, { kind: "step" }>; state: RunState; policyName: (id?: string | null) => string; deck: Deck; refresh: () => Promise<void> }) {
  const v = state.values;
  const meta = ts(item.at);
  switch (item.node) {
    case "research_company": {
      const p = v.profile;
      if (!p) return null;
      const reruns = state.events.filter((e) => e.node === "research_company" && e.status === "completed").length;
      if (item.occurrence < reruns - 1) return <ToolRow icon={Globe} summary="Profiled from the name alone (superseded below)" meta={meta} />;
      const company = state.advisor?.company;
      const counts = company
        ? (["VERIFIED", "ASSUMPTION", "UNKNOWN"] as const).map((k) => [k, company.facts.filter((f) => f.label === k).length] as const).filter(([, n]) => n > 0)
        : (["FACT", "INFERENCE", "ASSUMPTION", "UNKNOWN"] as const).map((k) => [k, p.facts.filter((f) => f.kind === k).length] as const).filter(([, n]) => n > 0);
      const webFacts = p.facts.filter((f) => f.kind === "FACT" && f.sources.some((x) => /^https?:/.test(x.url))).length;
      const sourced = webFacts > 0 ? ` · ${webFacts} web-sourced fact${webFacts === 1 ? "" : "s"}` : p.research_status === "OK" ? "" : " · no web sources";
      return (
        <ToolRow icon={Globe} summary={`Researched ${p.company_name}${sourced}`} meta={meta}>
          {p.overview && <p><Stream text={p.overview} /></p>}
          <div className="flex flex-wrap items-center gap-1.5">
            {counts.map(([k, n]) => (
              <span key={k} className="flex items-center gap-1 text-xs">
                <KindBadge kind={k} /> {n}
              </span>
            ))}
          </div>
          <ChipRow items={[p.industry, p.size, p.geography, p.workforce].filter((x): x is string => !!x && x.toLowerCase() !== "unknown")} />
          {p.research_note && <p className="text-xs text-muted-foreground"><Stream text={p.research_note} /></p>}
          {company?.market && company.market.status !== "OK" && <p className="text-xs text-muted-foreground">Industry context is unknown. It is not used as a policy score.</p>}
        </ToolRow>
      );
    }
    case "market_intelligence": {
      const market = state.advisor?.company?.market;
      return (
        <ToolRow icon={Globe} summary="Read the industry context" meta={meta}>
          <p className="text-xs text-muted-foreground">{market?.context || market?.note || "Industry context is unknown. It does not change the recommendation."}</p>
          {market?.hypotheses?.length ? <p className="text-xs">Working assumptions only: {market.hypotheses.join(" ")}</p> : null}
        </ToolRow>
      );
    }
    case "map_exposures": {
      const labelled = state.advisor?.company?.exposures;
      const ex = labelled || (v.exposures || []).map((e) => ({ title: e.title, description: e.description, label: e.status, rationale: e.reasoning }));
      return (
        <ToolRow icon={ListChecks} summary={`Mapped ${ex.length} health-cover need${ex.length === 1 ? "" : "s"}`} meta={meta}>
          <ul className="space-y-1">
            {ex.slice(0, 6).map((e) => (
              <li key={e.title} className="flex items-start gap-2">
                <KindBadge kind={e.label} className="mt-0.5 shrink-0" />
                <span>
                  <span className="text-ink">{e.title}</span> <span className="text-muted-foreground">— <Stream text={e.description} /></span>
                </span>
              </li>
            ))}
            {ex.length > 6 && <li className="text-xs text-quiet">+{ex.length - 6} more</li>}
          </ul>
        </ToolRow>
      );
    }
    case "policy_intelligence":
      return <ToolRow icon={FileSearch} summary="Read each brochure separately" meta={meta}><p className="text-xs text-muted-foreground">A passage that was not found stays not established. It is not treated as covered or excluded.</p></ToolRow>;
    case "compare_policies":
      return (
        <ToolRow icon={Columns3} summary={`Compared ${state.advisor?.comparison?.policies.length ?? v.policy_ids?.length ?? "the"} policies`} meta={meta} defaultOpen>
          {state.advisor ? (
            <>
              <ComparisonBrief view={state.advisor} runId={state.run.run_id} />
              <ScenarioPanel runId={state.run.run_id} />
            </>
          ) : <p className="text-muted-foreground">The comparison is not ready yet.</p>}
        </ToolRow>
      );
    case "policy_fit_arena": {
      const rec = state.advisor?.recommendation;
      const closeCall = state.events.some((e) => e.node === "confirm_recommendation" && e.status === "waiting");
      const summary = rec?.automatic && rec.policy_name ? `${rec.policy_name} · ${rec.fit_score}/100` : closeCall ? "The scores need your call" : "No automatic recommendation";
      return (
        <ToolRow icon={Gauge} summary={summary} meta={meta} defaultOpen>
          {state.advisor ? (
            <>
              <RecommendationBrief view={state.advisor} runId={state.run.run_id} />
              <RecommendationChange runId={state.run.run_id} onChanged={refresh} />
            </>
          ) : <p className="text-xs text-muted-foreground">The recommendation is not ready yet.</p>}
        </ToolRow>
      );
    }
    case "policy_check":
      return (
        <ToolRow icon={ShieldCheck} summary={state.advisor?.policy_check?.status === "COMPLETED" ? "Policy Check complete" : "Policy Check unavailable"} meta={meta} defaultOpen>
          {state.advisor ? <PolicyCheckBrief view={state.advisor} runId={state.run.run_id} /> : <p className="text-xs text-muted-foreground">Policy Check did not finish. The recommendation was not stress-tested.</p>}
        </ToolRow>
      );
    case "evidence_pack": {
      const pack = v.evidence_pack;
      if (!pack) return null;
      return (
        <ToolRow icon={FileSearch} summary={`Assembled ${pack.items.length} citable facts for ${shortName(policyName(pack.recommended_policy_id))}${pack.gaps.length ? ` · ${pack.gaps.length} gap${pack.gaps.length === 1 ? "" : "s"}` : ""}`} meta={meta}>
          {pack.gaps.length > 0 && (
            <div>
              <div className="text-xs text-muted-foreground">Gaps the deck must not paper over:</div>
              <ul className="list-disc space-y-0.5 pl-4 text-xs">
                {pack.gaps.slice(0, 4).map((g, i) => (
                  <li key={i}><Stream text={g} /></li>
                ))}
              </ul>
            </div>
          )}
        </ToolRow>
      );
    }
    case "generate_pitch": {
      const pitch = v.pitch;
      const latest = item.occurrence === (state.events.filter((e) => e.node === "generate_pitch" && e.status === "completed").length - 1);
      if (!pitch || !latest) return <ToolRow icon={Presentation} summary="Drafted a version of the deck (superseded)" meta={meta} />;
      // The audit that followed this draft recorded its version; edits since then bump the version without redrafting.
      const audits = state.events.filter((e) => e.node === "audit_pitch" && e.status === "completed" && e.created_at <= item.at).length;
      const drafted = v.audit_history?.[audits]?.pitch_version ?? pitch.version;
      return (
        <ToolRow icon={Presentation} summary={`Drafted v${drafted}: ${pitch.slides.length} slides${drafted !== pitch.version ? ` (now v${pitch.version} after your edits)` : ""}`} meta={meta} defaultOpen>
          <ol className="list-decimal space-y-0.5 pl-5 text-xs">
            {pitch.slides.map((s, i) => (
              <li key={s.slide_number}>
                <button type="button" className="focus-ring rounded-sm text-left hover:underline" onClick={() => deck.go(i)}>
                  {s.title}
                </button>
              </li>
            ))}
          </ol>
          {v.pitch_warnings?.length ? (
            <Callout tone="warn" compact icon={AlertTriangle} title="Generation notes">
              <ul className="list-disc space-y-0.5 pl-4 text-xs">
                {v.pitch_warnings.map((w, i) => (
                  <li key={i}><Stream text={w} /></li>
                ))}
              </ul>
            </Callout>
          ) : null}
        </ToolRow>
      );
    }
    case "audit_pitch": {
      const hist = v.audit_history || [];
      const s = hist[item.occurrence] ?? v.audit?.summary;
      if (!s) return null;
      const flagged = s.contradicted + s.not_found;
      return (
        <ToolRow icon={ShieldCheck} summary={<span className="flex items-center gap-2"><Stream text={`Audited draft v${"pitch_version" in s ? s.pitch_version : v.audit?.pitch_version}: ${s.total_claims} claims`} /> <GateBadge gate={s.gate} size="xs" /></span>} meta={meta}>
          <p className="text-xs text-muted-foreground">
            <Stream text={`${s.total_claims} claims (${s.material_claims} material): ${s.supported} supported${s.partially_supported ? `, ${s.partially_supported} partially` : ""}${flagged ? `, ${flagged} unsupported or contradicted` : ""}${s.uncertain ? `, ${s.uncertain} uncertain` : ""}.`} />
          </p>
        </ToolRow>
      );
    }
    case "export_outputs": {
      const outputs = state.run.outputs || {};
      return (
        <ToolRow icon={Download} summary="Exported the deck and the audit report" meta={meta} defaultOpen>
          <div className="flex flex-wrap gap-2">
            {DOWNLOADS.filter((d) => outputs[d.kind]).map((d) => (
              <Button key={d.kind} size="sm" variant="outline" render={<a href={api.downloadUrl(state.run.run_id, d.kind)} />}>
                <Download className="size-3.5" /> {d.label}
              </Button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">The PPTX is editable; every policy bullet keeps its brochure footnote. The audit report lists each claim with its Evidence Passport.</p>
        </ToolRow>
      );
    }
    default:
      return null;
  }
}

const ANSWER_LABEL: Record<string, string> = { continue: "Continue with assumptions", add_context: "Added what I know", keep: "Let the score decide", approve: "Approved", edit: "Saved edits — re-audit", regenerate: "Regenerate", reject: "Rejected" };
const QUESTION_LABEL: Record<string, string> = { context: "Asked for client context", close_call: "Asked which policy to pitch (close call)", review: "Asked for your review" };

function AnsweredQuestion({ item, state, policyName }: { item: Extract<ThreadItem, { kind: "question" }>; state: RunState; policyName: (id?: string | null) => string }) {
  const a = item.answered!;
  const review = state.values.review;
  let answer: React.ReactNode = ANSWER_LABEL[a] ?? `Pitch ${shortName(policyName(a))}`;
  if (item.question === "context" && a === "add_context") {
    const chips = intakeChips(state.values?.intake);
    answer = chips.length ? <ChipRow items={chips} className="justify-end" /> : ANSWER_LABEL[a];
  }
  if (item.question === "review" && review?.action === a && (review.reviewer || review.note || review.feedback)) {
    answer = (
      <>
        {ANSWER_LABEL[a]}
        {review.reviewer ? ` · ${review.reviewer}` : ""}
        {review.note || review.feedback ? <div className="mt-0.5 text-xs text-muted-foreground">{review.note || review.feedback}</div> : null}
      </>
    );
  }
  return (
    <>
      <ToolRow icon={MessageCircleQuestion} summary={QUESTION_LABEL[item.question] ?? "Asked a question"} meta={ts(item.at)} />
      <UserMessage>{answer}</UserMessage>
    </>
  );
}

function FailureMessage({ run, onRetry, retrying, retryError }: { run: RunSummary; onRetry: () => void; retrying: boolean; retryError: string | null }) {
  const quota = run.error_kind === "quota";
  const interrupted = run.error_kind === "interrupted";
  const failedAt = useMemo(() => toMs(run.updated_at), [run.updated_at]);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!quota) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [quota]);
  const remaining = quota && run.retry_after != null ? Math.max(0, run.retry_after - (now - failedAt) / 1000) : 0;
  const retryBtn = (
    <Button size="sm" variant={quota || interrupted ? "default" : "outline"} onClick={onRetry} loading={retrying} className="tabular-nums">
      <RotateCcw className="size-4" />
      {interrupted ? "Resume" : quota ? (remaining > 0 ? `Retry now (reset in ${formatWait(remaining)})` : "Retry now") : "Retry from the failed step"}
    </Button>
  );
  return (
    <Callout tone={quota || interrupted ? "warn" : "danger"} icon={interrupted ? Unplug : quota ? TimerReset : undefined} title={interrupted ? "Interrupted by a server restart" : quota ? "Gemini free-tier limit reached — paused, not lost" : "This step failed"} actions={retryBtn}>
      <span className="break-words">{run.error}</span>
      <p className="mt-1 text-xs text-muted-foreground">{quota ? `Progress is checkpointed; retrying resumes from the failed step with the same model. Retrying before the reset hits the same limit again.${run.retry_after != null ? ` Expected reset: ${formatWait(remaining)}.` : ""}` : "Completed steps are kept. Retrying re-runs only the step that failed."}</p>
      {retryError && <p className="mt-1 text-xs font-medium">Retry failed: {retryError}</p>}
    </Callout>
  );
}

export function Thread({ state, deck, policyName, refresh }: { state: RunState; deck: Deck; policyName: (id?: string | null) => string; refresh: () => Promise<void> }) {
  const items = useMemo(() => buildThread(state), [state]);
  const runId = state.run.run_id;
  const status = state.run.status;
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (status !== "running") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [status]);
  const onRetry = async () => {
    setRetrying(true);
    setRetryError(null);
    try {
      await api.retryRun(runId);
      await refresh();
    } catch (e) {
      setRetryError(e instanceof Error ? e.message : String(e));
    } finally {
      setRetrying(false);
    }
  };

  // Keep the newest message in view as the run progresses (the thread owns its scroll container).
  const boxRef = useRef<HTMLDivElement>(null);
  const key = `${items.length}:${status}:${state.question?.question ?? ""}`;
  useEffect(() => {
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [key]);

  // Items present when the thread mounted are history; anything appended later arrived live and streams in.
  const [mountCount] = useState(items.length);

  const lastEventAt = state.events.length ? state.events[state.events.length - 1].created_at : null;
  const stalled = status === "running" && !!lastEventAt && now - toMs(lastEventAt) > STALL_MS;
  const q = state.question;
  const intake = state.values?.intake ?? null;
  const companyName = storedCompanyName(intake?.company_name, state.run?.company_name);
  const pitchHeading = companyName ? `Pitch ${companyName}` : status === "running" ? "Loading" : intake ? "Unknown company" : "Company unavailable";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={boxRef} className="thin-scroll min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
        {items.map((item, i) => (
          <StreamScope key={i} value={i >= mountCount}>
            {(() => {
        switch (item.kind) {
          case "intake":
            return (
              <UserMessage key={i} meta={ts(item.at)}>
                <div className="font-medium">{pitchHeading}</div>
                {intake && <ChipRow items={intakeChips(intake)} className="mt-1 justify-end" />}
                {intake?.advisor_notes && <div className="mt-1 text-xs text-muted-foreground">{intake.advisor_notes}</div>}
              </UserMessage>
            );
          case "step":
            return <StepMessage key={i} item={item} state={state} policyName={policyName} deck={deck} refresh={refresh} />;
          case "question":
            if (item.answered) return <AnsweredQuestion key={i} item={item} state={state} policyName={policyName} />;
            if (!q || status !== "awaiting_review") return <ToolRow key={i} icon={MessageCircleQuestion} summary={QUESTION_LABEL[item.question]} meta={ts(item.at)} />;
            if (q.question === "context") return <ContextQuestion key={i} runId={runId} q={q} onDone={refresh} />;
            if (q.question === "close_call") return <CloseCallQuestion key={i} runId={runId} q={q} onDone={refresh} />;
            return <ReviewQuestion key={i} runId={runId} q={q} state={state} deck={deck} onDone={refresh} />; // "review", and checkpoints from before questions were typed
          case "working":
            return (
              <WorkingRow key={i} label={`${NODE_LABELS[item.node] || item.node}…`} detail={NODE_EXPLAIN[item.node]} meta={elapsed(item.at, now)}>
                {stalled && lastEventAt && (
                  <Callout tone="warn" icon={Hourglass} compact title={`No progress for ${fmtRelative(lastEventAt).replace(" ago", "")}`} actions={<Button size="xs" variant="outline" onClick={onRetry} loading={retrying}><RotateCcw className="size-3" /> Resume from last checkpoint</Button>}>
                    Long model calls take a couple of minutes, but silence this long usually means the backend restarted. Resuming re-runs only the interrupted step.
                    {retryError && <div className="mt-1 text-xs font-medium">{retryError}</div>}
                  </Callout>
                )}
              </WorkingRow>
            );
          case "failed":
            return (
              <WorkingRow key={i} state="error" label={`${NODE_LABELS[item.node] || item.node} failed`} meta={ts(item.at)} detail={status !== "failed" ? "Retried; see below." : undefined} />
            );
          case "retried":
            return <UserMessage key={i} meta={ts(item.at)}>{item.message || "Retry"}</UserMessage>;
          case "outcome":
            return item.status === "rejected" ? (
              <AssistantMessage key={i} title="Run closed without a deck" meta={ts(item.at)}>
                <p className="text-muted-foreground"><Stream text="Rejected drafts are not exported. Start a new pitch to try again with different context." /></p>
              </AssistantMessage>
            ) : null;
        }
            })()}
          </StreamScope>
        ))}
        {status === "failed" && <FailureMessage run={state.run} onRetry={onRetry} retrying={retrying} retryError={retryError} />}
        {status === "awaiting_review" && !q && (
          <StatusPill tone="warn" size="xs">
            Waiting on a question that is no longer pending — reload.
          </StatusPill>
        )}
      </div>
      <ThreadComposer state={state} refresh={refresh} policyName={policyName} />
    </div>
  );
}
