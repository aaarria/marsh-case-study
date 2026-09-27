"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowUp } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { Matrix } from "@/components/matrix";
import { Button } from "@/components/ui/button";
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
      <main className="thin-scroll flex flex-1 flex-col items-center overflow-y-auto px-6 py-10">
        <div className="w-full max-w-2xl space-y-6">
          {readiness.error && <ErrorBlock message={readiness.error} title="API unavailable" />}
          <SystemNotice r={readiness} detailed />
          <div className="pt-6 text-center">
            {/* The system's mark: the same dot matrix as the status bar, at hero scale. It twinkles while the run is being started and otherwise holds still. */}
            <Matrix variant="twinkle" state={submitting ? "working" : "done"} dot={6} rounded className="mx-auto mb-8" title={submitting ? "Starting the run" : "Ready"} />
            <h1 className="display text-3xl sm:text-4xl">Who are we pitching?</h1>
            <p className="mx-auto mt-3 max-w-lg text-sm text-muted-foreground">AI researches the company. Policy coverage is grounded in the supplied brochures. Unknowns remain unknown.</p>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
            className="panel overflow-hidden"
          >
            <div className="flex items-end gap-2 p-3">
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
                className="min-h-9 flex-1 resize-none bg-transparent px-1 py-1.5 text-lg text-ink outline-none placeholder:text-quiet"
              />
              <Button type="submit" size="icon" disabled={!canSend} loading={submitting} aria-label="Start pitch" title={name.length < 2 ? "Type the company name to start" : tooFew ? `Keep at least ${MIN_POLICIES} policies` : undefined}>
                <ArrowUp className="size-4" />
              </Button>
            </div>

            <div className="border-t border-hairline px-3 py-2">
              <ChipGroup label="Client priorities">
                {[...PRIORITY_SUGGESTIONS, ...priorities.filter((p) => !PRIORITY_SUGGESTIONS.includes(p))].map((p) => (
                  <Chip key={p} size="xs" selected={priorities.includes(p)} onClick={() => setPriorities((s) => (s.includes(p) ? s.filter((x) => x !== p) : [...s, p]))}>
                    {p}
                  </Chip>
                ))}
              </ChipGroup>
              <p className="mt-1.5 text-2xs text-quiet">Selected priorities directly shape policy fit.</p>
            </div>

            <div className="border-t border-hairline px-3 py-2">
              <ChipGroup label="Policies in scope">
                {policies.length === 0 && <span className="text-xs text-quiet">Loading brochures…</span>}
                {policies.map((p) => {
                  const on = selected.includes(p.policy_id);
                  return (
                    <Chip key={p.policy_id} size="xs" selected={on} onClick={() => setSelected((s) => (s.includes(p.policy_id) ? s.filter((x) => x !== p.policy_id) : [...s, p.policy_id]))} title={`${p.insurer} · ${p.pages} pages · Ready · ${on ? "Selected" : "Not selected"}`}>
                      {p.policy_name}
                      <span className="ml-1 text-quiet">{on ? "Ready · Selected" : "Ready · Not selected"}</span>
                    </Chip>
                  );
                })}
              </ChipGroup>
              <p className="mt-1.5 text-2xs text-quiet">Ready means the supplied brochure is ingested. Selected means it is in this comparison. Uploading a file does not add it.</p>
              {tooFew && <p className="tone-danger tint-text mt-1.5 text-xs">Keep at least {MIN_POLICIES} policies: a recommendation needs something to be compared against.</p>}
              <label className="mt-2 inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
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
                Store another PDF separately
              </label>
              {uploadNote && <p className="mt-1.5 text-xs text-muted-foreground">{uploadNote}</p>}
              {uploads.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {uploads.map((row, index) => (
                    <li key={`${row.original_name}-${index}`} className="text-xs text-muted-foreground">
                      {row.original_name} · {row.status === "Failed" ? "Failed" : "Uploaded"} · Not selected · Not in the four-policy comparison
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </form>

          {error && <ErrorBlock message={error} title="Cannot start" />}
          <p className="text-center text-xs text-quiet">Nothing is exported until you approve. Unknown stays unknown; the system never fills a gap with an invented figure.</p>
        </div>
      </main>
    </AppShell>
  );
}
