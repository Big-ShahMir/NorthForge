/**
 * Backend response types, generated from the OpenAPI schema.
 *
 * `components["schemas"][...]` types come from `src/lib/api-schema.d.ts`
 * (run `npm run generate:api` after the backend contract changes; see
 * `backend/northforge/api/export_openapi.py`). `Envelope<T>` stays
 * hand-written because openapi-typescript emits one concrete
 * `Envelope_<T>_` schema per response type (e.g. `Envelope_HealthData_`),
 * not a reusable generic -- this wrapper is that generic, shaped to match.
 */

import type { components } from "./api-schema";

export type ErrorBody = components["schemas"]["ErrorBody"];

export interface Envelope<T> {
  data: T | null;
  error: ErrorBody | null;
  request_id: string;
}

export type HealthData = components["schemas"]["HealthData"];
export type ReadinessData = components["schemas"]["ReadinessData"];
export type CheckData = components["schemas"]["CheckData"];

export type CheckStatus = CheckData["status"];
export type ReadinessStatus = ReadinessData["status"];
