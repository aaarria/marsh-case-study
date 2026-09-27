"use client";

import { useState } from "react";
import { CheckCircle2, Pencil, RefreshCw, ShieldCheck, Trash2, X, XCircle } from "lucide-react";
import { Stream } from "@/components/stream";
import { Button } from "@/components/ui/button";
import { Chip, ChipGroup } from "@/components/chip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Meter } from "@/components/meter";
import { AuditBadge, GateBadge, StatusPill } from "@/components/status-badge";
import { ErrorBlock } from "@/components/states";
import { QuestionFrame } from "@/components/thread/message";
import type { Deck } from "@/components/deck/use-deck";
import { api, ApiError } from "@/lib/api";
import { GATE_TONE, shortName } from "@/lib/format";
import { PRIORITY_SUGGESTIONS } from "@/lib/intake";
import type { Answer, ClaimAudit, Question, RunState } from "@/lib/types";
import { cn } from "@/lib/utils";

const ISSUE = new Set(["CONTRADICTED", "NOT_FOUND"]);
const JUDGEMENT = new Set(["PARTIALLY_SUPPORTED", "UNCERTAIN"]);

function useAnswer(runId: string, onDone: () => Promise<void> | void) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const send = async (label: string, body: Answer | (() => Promise<unknown>)) => {
    setBusy(label);
    setError(null);
    try {
      if (typeof body === "function") await body();
      else await api.answer(runId, body);
      await onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  return { busy, error, send };
}

/** "Only a name, and web research is off": add context or continue with labelled assumptions. */
export function ContextQuestion({ runId, q, onDone }: { runId: string; q: Question; onDone: () => Promise<void> }) {
  const { busy, error, send } = useAnswer(runId, onDone);
  const [f, setF] = useState({ industry: "", geography: "", employee_count: "", advisor_notes: "" });
  const [priorities, setPriorities] = useState<string[]>([]);
  const filled = Object.values(f).some((x) => x.trim()) || priorities.length > 0;
  const count = f.employee_count ? Number(f.employee_count) : undefined;
  const badCount = count !== undefined && (!Number.isInteger(count) || count < 1);
  return (
    <QuestionFrame title={<Stream text={q.message} />}>
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="q-industry">Industry</Label>
          <Input id="q-industry" value={f.industry} maxLength={120} onChange={(e) => setF({ ...f, industry: e.target.value })} placeholder="e.g. Retail" />
        </div>
        <div className="space-y-1">
          <Label htmlFor="q-geo">Geography</Label>
          <Input id="q-geo" value={f.geography} maxLength={120} onChange={(e) => setF({ ...f, geography: e.target.value })} placeholder="e.g. Pan-India" />
        </div>
        <div className="space-y-1">
          <Label htmlFor="q-count">Employees</Label>
          <Input id="q-count" type="number" min={1} value={f.employee_count} onChange={(e) => setF({ ...f, employee_count: e.target.value })} placeholder="e.g. 300" aria-invalid={badCount || undefined} />
        </div>
      </div>
      <Textarea rows={2} maxLength={2000} value={f.advisor_notes} onChange={(e) => setF({ ...f, advisor_notes: e.target.value })} placeholder="Advisor notes: workforce profile, current insurer pain points, renewal timing…" aria-label="Advisor notes" />
      <ChipGroup label="Priorities">
        {PRIORITY_SUGGESTIONS.map((p) => (
          <Chip key={p} size="xs" selected={priorities.includes(p)} onClick={() => setPriorities((s) => (s.includes(p) ? s.filter((x) => x !== p) : [...s, p]))}>
            {p}
          </Chip>
        ))}
      </ChipGroup>
      {error && <ErrorBlock message={error} className="mb-0" />}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" loading={busy === "add"} disabled={!!busy || !filled || badCount} onClick={() => send("add", { action: "add_context", industry: f.industry.trim() || undefined, geography: f.geography.trim() || undefined, employee_count: count, advisor_notes: f.advisor_notes.trim() || undefined, client_priorities: priorities.length ? priorities : undefined })}>
          Use this and continue
        </Button>
        <Button size="sm" variant="outline" loading={busy === "continue"} disabled={!!busy} onClick={() => send("continue", { action: "continue" })}>
          Continue with assumptions
        </Button>
      </div>
      <p className="text-xs text-quiet">What you add is recorded as a verified advisor input and re-runs the profile. Assumptions are labelled on every slide and never audited as fact.</p>
    </QuestionFrame>
  );
}

/** Two or more policies within the close-call margin: the advisor picks, or the score stands. */
export function CloseCallQuestion({ runId, q, onDone }: { runId: string; q: Question; onDone: () => Promise<void> }) {
  const { busy, error, send } = useAnswer(runId, onDone);
  const policies = q.options.filter((o) => o.id !== "keep");
  return (
    <QuestionFrame title={<Stream text={q.message} />}>
      <ul className="space-y-2">
        {policies.map((o) => (
          <li key={o.id} className={cn("rounded-lg border p-3", o.id === q.recommended ? "border-hairline-strong bg-raised/40" : "border-hairline")}>
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-medium text-ink">
                {shortName(o.label)}
                {o.id === q.recommended && <span className="ml-2 text-xs font-normal text-muted-foreground">highest score</span>}
              </span>
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {o.score}/100 · {o.confidence?.toLowerCase()} confidence
              </span>
            </div>
            <Meter value={(o.score ?? 0) / 100} size="xs" className="mt-2" />
            {o.explanation?.length ? (
              <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-body">
                {o.explanation.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            ) : null}
            <Button size="xs" variant="outline" className="mt-2" loading={busy === o.id} disabled={!!busy} onClick={() => send(o.id, { action: o.id })}>
              Pitch {shortName(o.label)}
            </Button>
          </li>
        ))}
      </ul>
      {error && <ErrorBlock message={error} className="mb-0" />}
      <Button size="sm" loading={busy === "keep"} disabled={!!busy} onClick={() => send("keep", { action: "keep" })}>
        Let the score decide
      </Button>
    </QuestionFrame>
  );
}

function ClaimRow({ c, deck, editable }: { c: ClaimAudit; deck: Deck; editable: boolean }) {
  const on = deck.idx === c.claim.slide - 1 && deck.activeBullet != null && deck.activeBullet === c.claim.bullet_index;
  const failing = c.checks.find((k) => k.passed === false);
  return (
    <li className={cn("rounded-md border border-hairline p-2.5", on && "border-hairline-bright bg-raised/50")}>
      <div className="flex items-start gap-2">
        <AuditBadge status={c.status} size="xs" className="mt-0.5 shrink-0" />
        <button type="button" className="focus-ring min-w-0 flex-1 rounded-sm text-left text-sm leading-snug text-ink hover:underline" onClick={() => deck.focus(c.claim.slide, c.claim.bullet_index ?? null)}>
          {c.claim.claim_text}
        </button>
      </div>
      {(failing || c.correction_hint) && <p className="mt-1 pl-0.5 text-xs text-muted-foreground">{c.correction_hint || failing?.detail}</p>}
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <span className="text-2xs text-quiet">Slide {c.claim.slide}</span>
        <Button size="xs" variant="ghost" onClick={() => deck.setPassport(c)}>
          Evidence Passport
        </Button>
        {editable && c.claim.bullet_index != null && (
          <>
            <Button size="xs" variant="ghost" onClick={() => deck.startEdit(c.claim.slide, c.claim.bullet_index)}>
              <Pencil /> Edit
            </Button>
            <Button size="xs" variant="ghost" onClick={() => deck.removeBullet(c.claim.slide, c.claim.bullet_index!)}>
              <Trash2 /> Remove bullet
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

/** Final review. Failing claims are actionable inline; edits made on the deck are saved from here. */
export function ReviewQuestion({ runId, q, state, deck, onDone }: { runId: string; q: Question; state: RunState; deck: Deck; onDone: () => Promise<void> }) {
  const { busy, error, send } = useAnswer(runId, onDone);
  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const [override, setOverride] = useState(false);
  const [regen, setRegen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const audit = deck.preview ?? state.values.audit;
  if (!audit) return null;
  const gate = audit.summary.gate;
  const issues = audit.claims.filter((c) => ISSUE.has(c.status));
  const judgement = audit.claims.filter((c) => JUDGEMENT.has(c.status));
  const editing = !!deck.draft;
  const needsName = gate === "FAIL" && override && !reviewer.trim();
  const stale = !!state.values.pitch_stale;
  const canApprove = !editing && !stale && (gate !== "FAIL" || override) && !needsName;

  return (
    <QuestionFrame
      title={
        <span className="flex flex-wrap items-center gap-2">
          <span>Draft v{q.pitch_version} is audited.</span>
          <GateBadge gate={gate} size="xs" />
          {deck.preview && (
            <StatusPill tone="warn" size="xs" variant="solid">
              preview of unsaved edits
            </StatusPill>
          )}
        </span>
      }
    >
      <p className="text-sm text-ink">
        Audit · {audit.summary.supported} supported · {audit.summary.partially_supported + audit.summary.uncertain} review · {audit.summary.contradicted} contradicted
        {audit.summary.not_found ? ` · ${audit.summary.not_found} not found` : ""}
      </p>
      <p className="text-xs text-muted-foreground">Status: {stale ? "The recommendation changed. Regenerate the pitch and wait for the new audit before approving." : gate === "PASS" ? "Clear to approve" : gate === "FAIL" ? "Blocked until the claims are fixed or you record an override" : "Review required"}</p>
      <p className={cn("text-sm", `tone-${GATE_TONE[gate] ?? "neutral"} tint-text`)}>
        {gate === "PASS" && `All ${audit.summary.material_claims} material policy claims are supported by the cited brochure text. Company statements and assumptions are labelled.`}
        {gate === "UNCERTAIN" && `${judgement.length} claim${judgement.length === 1 ? "" : "s"} need${judgement.length === 1 ? "s" : ""} your judgement; nothing is contradicted.`}
        {gate === "FAIL" && `${issues.length} claim${issues.length === 1 ? " is" : "s are"} unsupported or contradicted. Fix them on the deck, remove them, or override with your name.`}
      </p>

      {(issues.length > 0 || judgement.length > 0) && (
        <ul className="space-y-1.5">
          {[...issues, ...judgement].map((c) => (
            <ClaimRow key={c.claim.claim_id} c={c} deck={deck} editable />
          ))}
        </ul>
      )}

      {editing ? (
        <div className="rounded-lg border border-hairline-strong bg-raised/40 p-3">
          <div className="text-sm font-medium text-ink">{deck.dirty ? "You have unsaved edits on the deck." : "Editing the deck. Change anything on the right."}</div>
          <p className="mt-0.5 text-xs text-muted-foreground">Edits keep existing citations. New policy claims without a source fail the gate; preview before saving.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" loading={busy === "preview"} disabled={!!busy || !deck.dirty} onClick={() => send("preview", async () => deck.setPreview((await api.auditPreview(runId, deck.draft!)).audit))}>
              <ShieldCheck className="size-4" /> Preview audit
            </Button>
            <Button size="sm" loading={busy === "edit"} disabled={!!busy || !deck.dirty} onClick={() => send("edit", { action: "edit", slides: deck.draft!, reviewer: reviewer || undefined })}>
              Save &amp; re-audit
            </Button>
            <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => (!deck.dirty || window.confirm("Discard your unsaved slide edits?")) && deck.discard()}>
              <X className="size-4" /> {deck.dirty ? "Discard" : "Done"}
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="reviewer">Reviewer</Label>
              <Input id="reviewer" value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="Your name" maxLength={120} aria-invalid={needsName || undefined} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="note">Note</Label>
              <Input id="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Recorded with the decision" maxLength={1000} />
            </div>
          </div>
          {gate === "FAIL" && (
            <label className="flex cursor-pointer items-start gap-2.5 text-sm">
              <input type="checkbox" className="mt-0.5 size-3.5 accent-current" checked={override} onChange={(e) => setOverride(e.target.checked)} />
              <span>I have read the unresolved claims and take responsibility for sending this deck. The override is recorded with my name.</span>
            </label>
          )}
          {regen && (
            <div className="space-y-1">
              <Label htmlFor="feedback">What should change?</Label>
              <Textarea id="feedback" rows={3} maxLength={2000} value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="e.g. Lead with maternity and day-1 chronic cover; drop the pricing slide." />
              <p className="text-xs text-quiet">Your notes and the auditor&apos;s findings go back to the writer; the new draft is audited again before it returns here.</p>
            </div>
          )}
          {error && <ErrorBlock message={error} className="mb-0" />}
          <div className="flex flex-wrap gap-2">
            {regen ? (
              <>
                <Button size="sm" loading={busy === "regenerate"} disabled={!!busy} onClick={() => send("regenerate", { action: "regenerate", feedback: feedback || undefined, reviewer: reviewer || undefined, note: note || undefined })}>
                  <RefreshCw className="size-4" /> Regenerate draft
                </Button>
                <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => setRegen(false)}>
                  Back
                </Button>
              </>
            ) : (
              <>
                <Button size="sm" loading={busy === "approve"} disabled={!!busy || !canApprove} title={stale ? "Regenerate the pitch after the recommendation change" : gate === "FAIL" && !override ? "Acknowledge the failed gate to enable approval" : needsName ? "Overrides are recorded against a reviewer name" : undefined} onClick={() => send("approve", { action: "approve", reviewer: reviewer || undefined, note: note || undefined })}>
                  <CheckCircle2 className="size-4" /> Approve &amp; export
                </Button>
                <Button size="sm" variant="outline" disabled={!!busy} onClick={() => deck.startEdit(deck.idx + 1)}>
                  <Pencil className="size-4" /> Edit slides
                </Button>
                <Button size="sm" variant="outline" disabled={!!busy} onClick={() => setRegen(true)}>
                  <RefreshCw className="size-4" /> Regenerate
                </Button>
                <Button size="sm" variant="ghost" loading={busy === "reject"} disabled={!!busy} onClick={() => window.confirm("Reject this draft? The run ends without a deck.") && send("reject", { action: "reject", reviewer: reviewer || undefined, note: note || undefined })}>
                  <XCircle className="size-4" /> Reject
                </Button>
              </>
            )}
          </div>
        </>
      )}
    </QuestionFrame>
  );
}
