"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import type { Health } from "./types";

/** What the system can and cannot do right now, in product terms. Derived from /api/health. */
export interface Readiness {
  health: Health | null;
  error: string | null;
  loading: boolean;
  /** No GEMINI_API_KEY: runs complete but only advisor inputs are used; all other fields stay UNKNOWN. */
  llmOffline: boolean;
  /** GEMINI_MODEL is not on the published free-tier list; may bill or fail. */
  modelUnknown: boolean;
  /** Web research (the backend's native crawler) is off; company statements are model assumptions, not verified facts. */
  researchOff: boolean;
  /** Retrieval indexes are still being built. */
  indexBuilding: boolean;
}

export function useHealth(): Readiness {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((h) => {
        if (!cancelled) {
          setHealth(h);
          setError(null);
        }
      })
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);
  return {
    health,
    error,
    loading,
    llmOffline: !!health && !health.llm_configured,
    modelUnknown: !!health && health.llm_configured && !health.model_free_tier_known,
    researchOff: !!health && !health.research_configured,
    indexBuilding: !!health && !health.retrieval?.ready,
  };
}
