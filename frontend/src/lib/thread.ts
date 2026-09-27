import type { RunEvent, RunState } from "./types";

/**
 * The run as a conversation. Derived purely from the event log plus the current graph values,
 * so a reload reproduces the same thread and nothing is stored client-side.
 */
export type ThreadItem =
  | { kind: "intake"; at: string }
  | { kind: "step"; node: string; at: string; occurrence: number }
  | { kind: "working"; node: string; at: string }
  | { kind: "question"; node: string; at: string; answered?: string; question: string }
  | { kind: "failed"; node: string; at: string; message: string }
  | { kind: "retried"; at: string; message: string }
  | { kind: "outcome"; status: "approved" | "rejected"; at: string };

/** Steps whose completion is worth a message. Bookkeeping nodes (context / close-call checks) only speak when they ask. */
const SPOKEN = new Set(["research_company", "market_intelligence", "map_exposures", "policy_intelligence", "compare_policies", "policy_fit_arena", "policy_check", "evidence_pack", "generate_pitch", "audit_pitch", "export_outputs"]);

export function buildThread(state: RunState): ThreadItem[] {
  const items: ThreadItem[] = [];
  const events = state.events;
  const seen: Record<string, number> = {};
  let open: RunEvent | null = null; // the last `started` event without a completion

  items.push({ kind: "intake", at: state.run.created_at });
  for (const e of events) {
    if (e.status === "started") {
      open = e.node === "run" ? null : e;
      continue;
    }
    if (e.status === "completed") {
      if (open?.node === e.node) open = null;
      if (SPOKEN.has(e.node)) {
        const n = seen[e.node] ?? 0;
        seen[e.node] = n + 1;
        items.push({ kind: "step", node: e.node, at: e.created_at, occurrence: n });
      }
      continue;
    }
    if (e.status === "waiting") {
      open = null;
      items.push({ kind: "question", node: e.node, at: e.created_at, question: e.message || "" });
      continue;
    }
    if (e.status === "answer") {
      for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i];
        if (it.kind === "question" && it.node === e.node && !it.answered) {
          it.answered = e.message || "";
          break;
        }
      }
      continue;
    }
    if (e.status === "failed") {
      if (e.node === "run") continue; // node-level failure already recorded
      open = null;
      items.push({ kind: "failed", node: e.node, at: e.created_at, message: e.message || "" });
      continue;
    }
    if (e.status === "retried") items.push({ kind: "retried", at: e.created_at, message: e.message || "" });
  }

  const status = state.run.status;
  if (status === "approved" || status === "rejected") items.push({ kind: "outcome", status, at: state.run.updated_at });
  else if (status === "running" && open) items.push({ kind: "working", node: open.node, at: open.created_at });
  return items;
}
