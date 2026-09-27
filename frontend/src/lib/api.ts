import type { Answer, AuditReport, EvidenceLookup, Health, PolicyDocument, PolicyUploadStatus, RecommendationChangeResult, RunState, RunSummary, ScenarioResult, Slide, SlideBullet, StudioProposal } from "./types";

const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${API_URL}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  policies: () => request<{ policies: PolicyDocument[] }>("/api/policies"),
  policyUploads: () => request<{ uploads: PolicyUploadStatus[]; corpus: { policy_id: string; policy_name: string; status: string; in_comparison: boolean }[] }>("/api/policies/uploads"),
  analyze: (body: Record<string, unknown>) => request<{ run_id: string; status: string }>("/api/client/analyze", { method: "POST", body: JSON.stringify(body) }),
  runs: (limit = 20) => request<{ runs: RunSummary[] }>(`/api/runs?limit=${limit}`),
  deleteRun: (runId: string) => request<{ run_id: string; deleted: boolean }>(`/api/runs/${runId}`, { method: "DELETE" }),
  run: (runId: string) => request<RunState>(`/api/runs/${runId}`),
  retryRun: (runId: string) => request<{ run_id: string; status: string }>(`/api/runs/${runId}/retry`, { method: "POST" }),
  artifact: <T,>(runId: string, kind: string) => request<T>(`/api/runs/${runId}/artifacts/${kind}`),
  answer: (runId: string, body: Answer) => request<{ run_id: string; action: string }>(`/api/runs/${runId}/answer`, { method: "POST", body: JSON.stringify(body) }),
  rewrite: (runId: string, body: { slide_number: number; instruction: string; text: string; kind: string; source_chunk_ids: string[]; source_urls?: string[] }) => request<{ bullet: SlideBullet; note: string | null; audit?: { status: string; detail: string } }>(`/api/runs/${runId}/rewrite`, { method: "POST", body: JSON.stringify(body) }),
  evidence: (runId: string, policyId: string, feature: string) => request<EvidenceLookup>(`/api/runs/${runId}/evidence?policy_id=${encodeURIComponent(policyId)}&feature=${encodeURIComponent(feature)}`),
  documentUrl: (policyId: string, page?: number | null) => `${API_URL}/api/policies/${policyId}/document${page ? `#page=${page}` : ""}`,
  uploadPolicy: async (file: File) => {
    let res: Response;
    try {
      res = await fetch(`${API_URL}/api/policies/upload?filename=${encodeURIComponent(file.name)}`, { method: "POST", headers: { "Content-Type": "application/pdf" }, body: file });
    } catch {
      throw new ApiError(0, `Cannot reach the API at ${API_URL}. Is the backend running?`);
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, detail);
    }
    return res.json() as Promise<{ stored: boolean; status: string; selected: boolean; in_comparison: boolean; message: string; original_name: string }>;
  },
  scenario: (runId: string, text: string) => request<ScenarioResult>(`/api/runs/${runId}/scenario`, { method: "POST", body: JSON.stringify({ text }) }),
  pitchStudio: (runId: string, body: { slide_number: number; instruction: string }) => request<StudioProposal>(`/api/runs/${runId}/pitch-studio`, { method: "POST", body: JSON.stringify(body) }),
  recommendationChange: (runId: string, body: { instruction: string; reviewer?: string; override?: boolean; apply?: boolean }) => request<RecommendationChangeResult>(`/api/runs/${runId}/recommendation-change`, { method: "POST", body: JSON.stringify(body) }),
  auditPreview: (runId: string, slides: Slide[]) => request<{ audit: AuditReport; pitch_version: number }>(`/api/runs/${runId}/audit-preview`, { method: "POST", body: JSON.stringify({ slides }) }),
  downloadUrl: (runId: string, kind: string) => `${API_URL}/api/downloads/${runId}/${kind}`,
};
