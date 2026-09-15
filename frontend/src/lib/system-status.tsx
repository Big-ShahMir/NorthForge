import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { ApiError, apiGet } from "@/lib/api";
import type { HealthData, ReadinessData } from "@/lib/api-types";

export type SystemStatusState =
  | { phase: "loading" }
  | { phase: "error"; error: ApiError }
  | { phase: "loaded"; health: HealthData; readiness: ReadinessData; fetchedAt: Date };

export interface SystemStatus {
  state: SystemStatusState;
  isRefreshing: boolean;
  refresh: () => Promise<void>;
}

const SystemStatusContext = createContext<SystemStatus | null>(null);

async function fetchSystemStatus(): Promise<SystemStatusState> {
  try {
    const [health, readiness] = await Promise.all([
      apiGet<HealthData>("/health"),
      apiGet<ReadinessData>("/ready"),
    ]);
    return {
      phase: "loaded",
      health: health.data,
      readiness: readiness.data,
      fetchedAt: new Date(),
    };
  } catch (error) {
    if (error instanceof ApiError) {
      return { phase: "error", error };
    }
    return {
      phase: "error",
      error: new ApiError("Unexpected error while loading status.", "UNKNOWN", 0, null),
    };
  }
}

export function useSystemStatusQuery(pollIntervalMs: number): SystemStatus {
  const [state, setState] = useState<SystemStatusState>({ phase: "loading" });
  const [isRefreshing, setIsRefreshing] = useState(false);
  const inFlight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async () => {
    if (inFlight.current) return inFlight.current;
    setIsRefreshing(true);
    inFlight.current = fetchSystemStatus()
      .then((next) => setState(next))
      .finally(() => {
        inFlight.current = null;
        setIsRefreshing(false);
      });
    return inFlight.current;
  }, []);

  useEffect(() => {
    void refresh();
    if (pollIntervalMs <= 0) return;
    const timer = window.setInterval(() => void refresh(), pollIntervalMs);
    return () => window.clearInterval(timer);
  }, [refresh, pollIntervalMs]);

  return { state, isRefreshing, refresh };
}

export function SystemStatusProvider({
  children,
  pollIntervalMs = 15_000,
}: {
  children: ReactNode;
  pollIntervalMs?: number;
}) {
  const value = useSystemStatusQuery(pollIntervalMs);
  return <SystemStatusContext.Provider value={value}>{children}</SystemStatusContext.Provider>;
}

export function useSystemStatus(): SystemStatus {
  const value = useContext(SystemStatusContext);
  if (!value) {
    throw new Error("useSystemStatus must be used inside SystemStatusProvider");
  }
  return value;
}
