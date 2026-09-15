import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Envelope, HealthData, ReadinessData } from "@/lib/api-types";
import { SystemStatusProvider } from "@/lib/system-status";
import { SystemStatusPage } from "@/pages/SystemStatusPage";

const HEALTH: Envelope<HealthData> = {
  data: { status: "ok", version: "0.1.0", app_env: "test" },
  error: null,
  request_id: "req-health",
};

function readiness(status: ReadinessData["status"], checks: ReadinessData["checks"]) {
  const body: Envelope<ReadinessData> = {
    data: { status, checks },
    error: null,
    request_id: "req-ready",
  };
  return body;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "X-Request-ID": "req-1" },
  });
}

function mockFetch(handler: (path: string) => Response | Promise<Response>) {
  const spy = vi.fn((input: RequestInfo | URL) => Promise.resolve(handler(String(input))));
  vi.stubGlobal("fetch", spy);
  return spy;
}

function renderPage() {
  return render(
    <SystemStatusProvider pollIntervalMs={0}>
      <SystemStatusPage />
    </SystemStatusProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SystemStatusPage", () => {
  it("shows a loading state and then every dependency check", async () => {
    mockFetch((path) =>
      path === "/health"
        ? jsonResponse(HEALTH)
        : jsonResponse(
            readiness("ready", [
              { name: "postgres", status: "ok", latency_ms: 3.2, detail: null },
              { name: "redis", status: "ok", latency_ms: 1.1, detail: null },
              { name: "worker", status: "ok", latency_ms: 0.9, detail: "j_complete=0" },
            ]),
          ),
    );
    renderPage();

    expect(screen.getByRole("status")).toHaveTextContent("Loading system status");
    expect(await screen.findByText("PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("Redis")).toBeInTheDocument();
    expect(screen.getByText("Queue worker")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText(/Version 0\.1\.0/)).toBeInTheDocument();
    expect(within(screen.getByRole("list")).getAllByText("OK")).toHaveLength(3);
  });

  it("renders a 503 readiness response as not ready without throwing", async () => {
    mockFetch((path) =>
      path === "/health"
        ? jsonResponse(HEALTH)
        : jsonResponse(
            readiness("not_ready", [
              { name: "postgres", status: "error", latency_ms: 500, detail: "connection failed" },
              { name: "redis", status: "ok", latency_ms: 1, detail: null },
              {
                name: "worker",
                status: "unavailable",
                latency_ms: 1,
                detail: "no recent worker heartbeat",
              },
            ]),
            503,
          ),
    );
    renderPage();

    expect(await screen.findByText(/Not ready/)).toBeInTheDocument();
    expect(screen.getByText("connection failed")).toBeInTheDocument();
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
  });

  it("shows an error state with the code when the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load system status");
    expect(alert).toHaveTextContent("NETWORK_ERROR");
  });

  it("surfaces an API error envelope with its request id", async () => {
    mockFetch(() =>
      jsonResponse(
        {
          data: null,
          error: { code: "INTERNAL_ERROR", message: "Boom.", details: null },
          request_id: "req-500",
        },
        500,
      ),
    );
    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Boom.");
    expect(alert).toHaveTextContent("INTERNAL_ERROR");
    expect(alert).toHaveTextContent("req-500");
  });

  it("refetches when Refresh is clicked", async () => {
    const spy = mockFetch((path) =>
      path === "/health" ? jsonResponse(HEALTH) : jsonResponse(readiness("degraded", [])),
    );
    renderPage();
    await screen.findByText(/Degraded/);
    const callsAfterLoad = spy.mock.calls.length;

    await userEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => expect(spy.mock.calls.length).toBe(callsAfterLoad + 2));
  });
});
