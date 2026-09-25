"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { RunState } from "./types";

const ACTIVE = new Set(["running"]);

/** Polls a run. Polls fast while running, slowly once the run is waiting on a human or finished. */
export function useRun(runId: string | null, intervalMs = 2000) {
  const [state, setState] = useState<RunState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!!runId);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const statusRef = useRef<string>("running");

  const refresh = useCallback(async () => {
    if (!runId) return;
    try {
      const s = await api.run(runId);
      statusRef.current = s.run.status;
      setState(s);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    const tick = async () => {
      await refresh();
      if (cancelled) return;
      const idle = !ACTIVE.has(statusRef.current);
      timer.current = setTimeout(tick, idle ? intervalMs * 5 : intervalMs);
    };
    tick();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [runId, refresh, intervalMs]);

  const isActive = !!state && ACTIVE.has(state.run.status);
  return { state, error, loading, refresh, isActive };
}
