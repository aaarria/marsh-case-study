"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowUp } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Chip, ChipGroup } from "@/components/chip";
import { ErrorBlock } from "@/components/states";
import { SystemNotice } from "@/components/system-notice";
import { api, ApiError } from "@/lib/api";
import { useHealth } from "@/lib/use-health";
import { PRIORITY_SUGGESTIONS } from "@/lib/intake";
import type { PolicyDocument, PolicyUploadStatus } from "@/lib/types";

const MIN_POLICIES = 2;

/** One composer, like a chat: name the client, optionally add what you know, send. The thread asks for anything else it needs. */
export default function Composer() {
  const router = useRouter();
  const readiness = useHealth();
  const [policies, setPolicies] = useState<PolicyDocument[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [company, setCompany] = useState("");
  const [priorities, setPriorities] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploadNote, setUploadNote] = useState<string | null>(null);
  const [uploads, setUploads] = useState<PolicyUploadStatus[]>([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .policies()
      .then((p) => {
        setPolicies(p.policies);
        setSelected(p.policies.map((x) => x.policy_id));
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
    api.policyUploads().then((rows) => setUploads(rows.uploads)).catch(() => setUploads([]));
  }, []);

  const name = company.trim();
  const tooFew = policies.length > 0 && selected.length < MIN_POLICIES;
  const canSend = name.length >= 2 && policies.length > 0 && !tooFew && !submitting && !readiness.indexBuilding;

  const send = async () => {
    if (!canSend) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.analyze({
        company_name: name,
        client_priorities: priorities,
        selected_policy_ids: selected.length === policies.length ? null : selected,
      });
      router.push(`/runs/${res.run_id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setSubmitting(false);
    }
  };

  return (
    <AppShell>
      <main className="thin-scroll flex flex-1 flex-col items-center overflow-y-auto px-4 py-8 sm:px-8 sm:py-14">
        <div className="w-full max-w-2xl space-y-8 sm:space-y-10">
          {readiness.error && <ErrorBlock message={readiness.error} title="API unavailable" />}
          <SystemNotice r={readiness} detailed />
          <div className="pt-2 text-center">
            <h1 className="display text-3xl sm:text-4xl lg:text-5xl">Marsh Health Policy Advisory</h1>
            <p className="mx-auto mt-3 max-w-lg text-sm text-muted-foreground">Evidence-led health insurance comparison and advisory system</p>
            <details className="mx-auto mt-4 max-w-md text-left">
              <summary className="cursor-pointer text-sm text-ink">Who are we advising?</summary>
              <p className="mt-2 text-sm text-muted-foreground">The comparison follows the client&apos;s stated priorities, the wording in the supplied brochures, and the existing recommendation logic. Unresolved points stay unresolved.</p>
              <ol className="mt-3 space-y-1 text-sm text-muted-foreground">
                <li>1. Enter the company</li>
                <li>2. Research the company</li>
                <li>3. Select client priorities</li>
                <li>4. Compare the policies</li>
                <li>5. Receive a recommendation and alternatives</li>
                <li>6. Build the advisory pitch</li>
              </ol>
            </details>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
            className="panel overflow-hidden"
          >
            <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-end sm:p-5">
              <textarea
                autoFocus
                rows={1}
                value={company}
                maxLength={120}
                onChange={(e) => setCompany(e.target.value.replace(/\n/g, ""))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                placeholder="Company name, e.g. Tata Consultancy Services"
                aria-label="Company name"
                className="min-h-11 flex-1 resize-none rounded-md border border-hairline bg-surface px-3 py-2 text-lg text-ink outline-none placeholder:text-quiet focus:border-marsh-navy"
              />
              <Button type="submit" disabled={!canSend} loading={submitting} className="w-full sm:w-auto" aria-label="Start pitch" title={name.length < 2 ? "Type the company name to start" : tooFew ? `Keep at least ${MIN_POLICIES} policies` : undefined}>
                Start pitch <ArrowUp className="size-4" />
              </Button>
            </div>

            <div className="border-t border-hairline px-5 py-4">
              <ChipGroup label="Client priorities">
                {[...PRIORITY_SUGGESTIONS, ...priorities.filter((p) => !PRIORITY_SUGGESTIONS.includes(p))].map((p) => (
                  <Chip key={p} size="xs" selected={priorities.includes(p)} onClick={() => setPriorities((s) => (s.includes(p) ? s.filter((x) => x !== p) : [...s, p]))}>
                    {p}
                  </Chip>
                ))}
              </ChipGroup>
              <p className="mt-1.5 text-2xs text-quiet">Selected priorities directly shape policy fit. Leave them empty for a baseline comparison.</p>
            </div>

            <div className="border-t border-hairline px-5 py-5">
              <p className="text-sm text-ink">Select the policy brochures to compare</p>
              <p className="mt-1 text-2xs text-quiet">{policies.length === 0 ? "Loading the ingested brochures…" : `Comparing ${selected.length} ingested brochure${selected.length === 1 ? "" : "s"}.`}</p>
              {tooFew && <p className="tone-danger tint-text mt-1.5 text-xs">Keep at least {MIN_POLICIES} policies: a recommendation needs something to be compared against.</p>}
              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                {policies.map((p) => {
                  const on = selected.includes(p.policy_id);
                  return (
                    <button
                      key={p.policy_id}
                      type="button"
                      aria-pressed={on}
                      onClick={() => setSelected((s) => (s.includes(p.policy_id) ? s.filter((x) => x !== p.policy_id) : [...s, p.policy_id]))}
                      className={cn("flex min-h-20 flex-col items-start justify-between rounded-md border px-4 py-3 text-left transition-colors", on ? "border-marsh-navy bg-marsh-navy text-white" : "border-hairline-strong bg-surface text-ink hover:border-marsh-navy")}
                    >
                      <span className={cn("text-2xs font-semibold tracking-wide", on ? "text-white/80" : "text-quiet")}>{on ? "SELECTED" : "NOT SELECTED"}</span>
                      <span className="text-sm font-medium">{p.policy_name}</span>
                    </button>
                  );
                })}
              </div>
              <div className="mt-3">
                <label className="inline-flex cursor-pointer rounded-md border border-marsh-navy bg-surface px-4 py-2.5 text-sm font-medium text-marsh-navy hover:bg-marsh-cream">
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    className="sr-only"
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";
                      if (!file) return;
                      setError(null);
                      try {
                        const stored = await api.uploadPolicy(file);
                        setUploadNote(stored.message);
                        const rows = await api.policyUploads();
                        setUploads(rows.uploads);
                      } catch (err) {
                        setUploadNote(null);
                        setError(err instanceof ApiError ? err.message : "That file could not be stored.");
                      }
                    }}
                  />
                  + Add another policy
                </label>
                <p className="mt-1 text-2xs text-quiet">Upload an external brochure. It is stored for the evidence workflow and is not added to this comparison until it has been ingested.</p>
                {uploadNote && <p className="mt-1.5 text-xs text-muted-foreground">{uploadNote}</p>}
                {uploads.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {uploads.map((row, index) => (
                      <li key={`${row.original_name}-${index}`} className="text-xs text-muted-foreground">
                        {row.original_name} · {row.status === "Failed" ? "Could not be stored" : "Stored"} · not in this comparison
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </form>

          {error && <ErrorBlock message={error} title="Cannot start" />}
          <p className="text-center text-xs text-quiet">Recommendations are based on the evidence available in the supplied policy documents. Information that cannot be established remains clearly identified.</p>
        </div>
      </main>
    </AppShell>
  );
}
