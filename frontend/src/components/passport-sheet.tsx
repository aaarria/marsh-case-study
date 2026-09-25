"use client";

import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Callout } from "@/components/callout";
import { AuditBadge, StatusPill, ToneTag } from "@/components/status-badge";
import { ConditionNote, SourceList, SourceQuote } from "@/components/source-ref";
import { Field, Kicker } from "@/components/section";
import type { ClaimAudit } from "@/lib/types";
import { checkTone, titleCase } from "@/lib/format";

const CHECK_LABEL: Record<string, string> = { citation: "Citation", numerical: "Numbers", exclusion: "Exclusions", contradiction: "Contradiction", coverage: "Coverage" };

/** Evidence Passport for one claim, as a right-hand sheet so the reviewer keeps their place in the list. */
export function PassportSheet({ claim, onClose }: { claim: ClaimAudit | null; onClose: () => void }) {
  return (
    <Sheet open={!!claim} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="thin-scroll w-full overflow-y-auto sm:max-w-xl">
        {claim && (
          <>
            <SheetHeader className="pr-10">
              <div className="flex flex-wrap items-center gap-2">
                <SheetTitle>Evidence Passport</SheetTitle>
                <AuditBadge status={claim.status} />
                <StatusPill tone="neutral" size="xs" variant="dot">
                  {claim.claim.claim_type}
                  {claim.claim.material ? " · material" : ""}
                </StatusPill>
              </div>
              <SheetDescription className="text-ink">{claim.claim.claim_text}</SheetDescription>
            </SheetHeader>

            <div className="space-y-5 px-4 pb-6">
              <div>
                <Kicker className="mb-1.5">Checks</Kicker>
                <ul className="space-y-1.5">
                  {claim.checks.map((k) => (
                    <li key={k.check} className="flex items-start gap-2 text-sm">
                      <ToneTag tone={checkTone(k.passed)} size="sm" className="w-26 shrink-0 justify-center">
                        {CHECK_LABEL[k.check] || k.check}
                      </ToneTag>
                      <span className="min-w-0 leading-snug">
                        <span className="font-medium text-ink">{titleCase(k.status.toLowerCase())}</span>
                        <span className="text-muted-foreground"> — {k.detail}</span>
                      </span>
                    </li>
                  ))}
                </ul>
                {claim.correction_hint && (
                  <Callout tone="warn" compact className="mt-2" icon={null}>
                    Correction hint: {claim.correction_hint}
                  </Callout>
                )}
              </div>

              <div>
                <Kicker className="mb-1.5">Provenance</Kicker>
                <dl className="panel divide-y divide-hairline px-3">
                  <Field label="Policy">{claim.passport.policy_name || claim.claim.policy_id}</Field>
                  <Field label="Page">{claim.passport.page}</Field>
                  <Field label="Section">{claim.passport.section}</Field>
                  <Field label="Clause">{claim.passport.clause}</Field>
                  <Field label="Feature">{claim.claim.feature_key}</Field>
                  <Field label="Numbers in claim">{claim.claim.numbers.length ? claim.claim.numbers.join(", ") : null}</Field>
                  <Field label="Retrieval relevance">
                    {claim.passport.retrieval_relevance != null ? (
                      <span title="Ranking signal only">
                        {claim.passport.retrieval_relevance} <span className="text-muted-foreground">(ranking signal, not accuracy)</span>
                      </span>
                    ) : null}
                  </Field>
                  <Field label="Action">{claim.action}</Field>
                  <Field label="Claim id" mono>
                    {claim.claim.claim_id}
                  </Field>
                </dl>
              </div>

              {claim.passport.source_text && (
                <div>
                  <Kicker className="mb-1.5">Cited brochure text</Kicker>
                  <SourceQuote className="max-h-56">{claim.passport.source_text}</SourceQuote>
                </div>
              )}
              {claim.passport.linked_conditions.length > 0 && (
                <div>
                  <Kicker className="mb-1.5">Linked conditions</Kicker>
                  <ul className="space-y-1">
                    {claim.passport.linked_conditions.map((c, i) => (
                      <ConditionNote key={i}>{c}</ConditionNote>
                    ))}
                  </ul>
                </div>
              )}
              <div>
                <Kicker className="mb-1.5">Evidence retrieved</Kicker>
                <SourceList sources={claim.evidence} max={6} />
              </div>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
