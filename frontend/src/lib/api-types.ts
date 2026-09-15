/**
 * Hand-maintained mirror of the backend response contracts.
 * Keep in sync with backend/northforge/api/envelope.py and routes/system.py.
 */

export interface ErrorBody {
  code: string;
  message: string;
  details: unknown[] | Record<string, unknown> | null;
}

export interface Envelope<T> {
  data: T | null;
  error: ErrorBody | null;
  request_id: string;
}

export interface HealthData {
  status: string;
  version: string;
  app_env: string;
}

export type CheckStatus = "ok" | "error" | "unavailable";
export type ReadinessStatus = "ready" | "degraded" | "not_ready";

export interface CheckData {
  name: string;
  status: CheckStatus;
  latency_ms: number;
  detail: string | null;
}

export interface ReadinessData {
  status: ReadinessStatus;
  checks: CheckData[];
}
