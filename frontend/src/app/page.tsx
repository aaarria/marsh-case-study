"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowUp, ChevronDown } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { Matrix } from "@/components/matrix";
import { Button } from "@/components/ui/button";
import { Chip, ChipGroup } from "@/components/chip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ErrorBlock } from "@/components/states";
import { SystemNotice } from "@/components/system-notice";
import { api, ApiError } from "@/lib/api";
import { useHealth } from "@/lib/use-health";
import { PRIORITY_SUGGESTIONS } from "@/lib/intake";
import type { PolicyDocument } from "@/lib/types";
import { cn } from "@/lib/utils";

const MIN_POLICIES = 2;

/** One composer, like a chat: name the client, optionally add what you know, send. The thread asks for anything else it needs. */
export default function Composer() {
  const router = useRouter();
  const readiness = useHealth();
  const [policies, setPolicies] = useState<PolicyDocument[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [company, setCompany] = useState("");
  const [ctx, setCtx] = useState({ industry: "", geography: "", employee_count: "", advisor_notes: "" });
  const [priorities, setPriorities] = useState<string[]>([]);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .policies()
      .then((p) => {
        setPolicies(p.policies);
        setSelected(p.policies.map((x) => x.policy_id));
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  const name = company.trim();
  const tooFew = policies.length > 0 && selected.length < MIN_POLICIES;
  const ctxCount = Object.values(ctx).filter((x) => x.trim()).length + (priorities.length ? 1 : 0);
  const canSend = name.length >= 2 && policies.length > 0 && !tooFew && !submitting && !readiness.indexBuilding;

  const send = async () => {
    if (!canSend) return;
    const count = ctx.employee_count ? Number(ctx.employee_count) : undefined;
    if (count !== undefined && (!Number.isInteger(count) || count < 1)) return setError("Employee count must be a positive whole number.");
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.analyze({
        company_name: name,
        industry: ctx.industry.trim() || null,
        geography: ctx.geography.trim() || null,
        employee_count: count ?? null,
        advisor_notes: ctx.advisor_notes.trim() || null,
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
            <p className="mx-auto mt-3 max-w-lg text-sm text-muted-foreground">Name the client and send. The thread compares the brochures, drafts a short deck, audits every claim, and asks you only when a decision is genuinely yours.</p>
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
            </div>

            <div className="border-t border-hairline">
              <button type="button" onClick={() => setMore(!more)} aria-expanded={more} className="focus-ring flex w-full items-center justify-between px-3 py-2 text-sm text-body hover:text-ink">
                <span>
                  What you already know <span className="text-muted-foreground">({readiness.researchOff ? "recommended: web research is off" : "optional"}{ctxCount ? ` · ${ctxCount} filled` : ""})</span>
                </span>
                <ChevronDown className={cn("size-4 text-muted-foreground transition-transform duration-(--dur-base)", more && "rotate-180")} />
              </button>
              {more && (
                <div className="grid gap-3 px-3 pb-3 sm:grid-cols-3">
                  <div className="space-y-1">
                    <Label htmlFor="industry">Industry</Label>
                    <Input id="industry" value={ctx.industry} maxLength={120} onChange={(e) => setCtx({ ...ctx, industry: e.target.value })} placeholder="e.g. IT services" />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="geo">Geography</Label>
                    <Input id="geo" value={ctx.geography} maxLength={120} onChange={(e) => setCtx({ ...ctx, geography: e.target.value })} placeholder="e.g. Pan-India" />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="count">Employees</Label>
                    <Input id="count" type="number" min={1} max={5_000_000} value={ctx.employee_count} onChange={(e) => setCtx({ ...ctx, employee_count: e.target.value })} placeholder="e.g. 5000" />
                  </div>
                  <div className="space-y-1 sm:col-span-3">
                    <Label htmlFor="notes">Advisor notes</Label>
                    <Textarea id="notes" rows={2} maxLength={2000} value={ctx.advisor_notes} onChange={(e) => setCtx({ ...ctx, advisor_notes: e.target.value })} placeholder="Workforce profile, current insurer pain points, budget signals, renewal timing…" />
                  </div>
                </div>
              )}
            </div>

            <div className="border-t border-hairline px-3 py-2">
              <ChipGroup label="Policies in scope">
                {policies.length === 0 && <span className="text-xs text-quiet">Loading brochures…</span>}
                {policies.map((p) => (
                  <Chip key={p.policy_id} size="xs" selected={selected.includes(p.policy_id)} onClick={() => setSelected((s) => (s.includes(p.policy_id) ? s.filter((x) => x !== p.policy_id) : [...s, p.policy_id]))} title={`${p.insurer} · ${p.pages} pages`}>
                    {p.policy_name}
                  </Chip>
                ))}
              </ChipGroup>
              {tooFew && <p className="tone-danger tint-text mt-1.5 text-xs">Keep at least {MIN_POLICIES} policies: a recommendation needs something to be compared against.</p>}
            </div>
          </form>

          {error && <ErrorBlock message={error} title="Cannot start" />}
          <p className="text-center text-xs text-quiet">Nothing is exported until you approve. Unknown stays unknown; the system never fills a gap with an invented figure.</p>
        </div>
      </main>
    </AppShell>
  );
}
