import type { Envelope } from "./api-types";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;

  constructor(message: string, code: string, status: number, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

/**
 * Fetch an enveloped JSON resource. Resolves with `{ data, status }` even for
 * non-2xx responses that still carry `data` (readiness returns 503 with checks).
 * Throws `ApiError` when the envelope carries an error or the response is not JSON.
 */
export async function apiGet<T>(
  path: string,
  init?: RequestInit,
): Promise<{ data: T; status: number; requestId: string }> {
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError("The API could not be reached.", "NETWORK_ERROR", 0, null);
  }

  const requestId = response.headers.get("X-Request-ID");
  let body: Envelope<T>;
  try {
    body = (await response.json()) as Envelope<T>;
  } catch {
    throw new ApiError(
      `The API returned a non-JSON response (HTTP ${response.status}).`,
      "INVALID_RESPONSE",
      response.status,
      requestId,
    );
  }

  if (body.error) {
    throw new ApiError(body.error.message, body.error.code, response.status, body.request_id);
  }
  if (body.data === null || body.data === undefined) {
    throw new ApiError(
      "The API returned an empty response.",
      "EMPTY_RESPONSE",
      response.status,
      body.request_id,
    );
  }
  return { data: body.data, status: response.status, requestId: body.request_id };
}
