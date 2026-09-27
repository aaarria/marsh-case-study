import type { Tone } from "@/components/callout";
import type { AuditStatus } from "./types";

/** Every status family maps to one of the system's tones; components never pick colours themselves. */
export const AUDIT_TONE: Record<AuditStatus, Tone> = {
  SUPPORTED: "ok",
  PARTIALLY_SUPPORTED: "warn",
  CONTRADICTED: "danger",
  NOT_FOUND: "danger",
  UNCERTAIN: "warn",
  NOT_APPLICABLE: "neutral",
};

export const GATE_TONE: Record<string, Tone> = { PASS: "ok", UNCERTAIN: "warn", FAIL: "danger" };

export const KIND_TONE: Record<string, Tone> = { FACT: "ok", VERIFIED: "ok", INFERENCE: "info", ASSUMPTION: "warn", UNKNOWN: "neutral" };

const RUN_STATUS_TONE: Record<string, Tone> = {
  running: "info",
  awaiting_review: "warn",
  approved: "ok",
  completed: "ok",
  rejected: "neutral",
  failed: "danger",
};

const RUN_STATUS_LABEL: Record<string, string> = {
  running: "Generating",
  awaiting_review: "Needs you",
  approved: "Approved",
  completed: "Completed",
  rejected: "Rejected",
  failed: "Failed",
};

/**
 * A failed run is not one thing: a quota pause and a restart interruption are recoverable and
 * should not read as "Failed". Everything that shows a run's state goes through this.
 */
export function runStatus(run: { status: string; error_kind?: string | null }): { label: string; tone: Tone; recoverable: boolean } {
  if (run.status === "failed" && run.error_kind === "quota") return { label: "Paused · quota", tone: "warn", recoverable: true };
  if (run.status === "failed" && run.error_kind === "interrupted") return { label: "Interrupted", tone: "warn", recoverable: true };
  return { label: RUN_STATUS_LABEL[run.status] ?? titleCase(run.status), tone: RUN_STATUS_TONE[run.status] ?? "neutral", recoverable: run.status === "failed" && run.error_kind !== undefined };
}

export function checkTone(passed: boolean | null): Tone {
  return passed === true ? "ok" : passed === false ? "danger" : "neutral";
}

export function fmtRelative(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  if (isNaN(d.getTime())) return iso;
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d ago`;
  return d.toLocaleDateString();
}

export function shortName(name?: string | null): string {
  if (!name) return "";
  return name.replace("HDFC ERGO ", "HDFC ").replace("Care Health ", "").replace("Aditya Birla ", "ABHI ").replace("Niva Bupa ", "Niva ");
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
