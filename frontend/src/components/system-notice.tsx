"use client";

import { Globe, Loader2, ServerOff, ShieldAlert } from "lucide-react";
import { Callout } from "@/components/callout";
import type { Readiness } from "@/lib/use-health";
import { cn } from "@/lib/utils";

/**
 * Tells the advisor, before they start, what the system can and cannot do right now.
 * Shown where a run is started (dashboard, intake). Silent when everything is on.
 * `detailed` adds the operational consequence for the run about to be started.
 */
export function SystemNotice({ r, detailed, className }: { r: Readiness; detailed?: boolean; className?: string }) {
  if (r.loading || r.error) return null;
  const items: React.ReactNode[] = [];
  if (r.llmOffline) {
    items.push(
      <Callout key="llm" tone="danger" icon={ServerOff} title="Language model is offline (no GEMINI_API_KEY)">
        Runs still complete, but company research, exposure reasoning and the slide text cannot be generated. Only what you type on this form is used; every other field is recorded as UNKNOWN. {detailed && "Add the key to .env and restart the backend before pitching a real client."}
      </Callout>,
    );
  } else if (r.modelUnknown) {
    items.push(
      <Callout key="model" tone="warn" icon={ShieldAlert} title={`${r.health?.llm_model} is not on Google's published free-tier list`}>
        The app never switches models on its own. Set GEMINI_MODEL to a free-tier model to avoid unexpected billing or failures.
      </Callout>,
    );
  }
  if (!r.llmOffline && r.researchOff) {
    items.push(
      <Callout key="research" tone="info" icon={Globe} title="Web research is off: company facts come from you">
        WEB_RESEARCH_ENABLED is false, so nothing about the client is looked up (set it to true to have the backend read Wikipedia, the company website and search results itself; no API or key involved). Statements the model makes about the company are labelled ASSUMPTION and never audited as fact. {detailed ? "Anything you add below is recorded as a verified advisor input and drives the exposures directly." : "Add what you know on the intake form; it is the only verified source."}
      </Callout>,
    );
  }
  if (r.indexBuilding) {
    items.push(
      <Callout key="index" tone="warn" icon={Loader2} title="Brochure index is still being built">
        Policy comparison will be unavailable for a minute or two. Runs started now may fail at the comparison step; they can be retried.
      </Callout>,
    );
  }
  if (!items.length) return null;
  return <div className={cn("space-y-3", className)}>{items}</div>;
}
