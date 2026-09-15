import { AlertTriangle, CheckCircle2, HelpCircle, RefreshCw, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { CheckData, CheckStatus, ReadinessStatus } from "@/lib/api-types";
import { useSystemStatus } from "@/lib/system-status";

const CHECK_LABELS: Record<string, string> = {
  postgres: "PostgreSQL",
  redis: "Redis",
  worker: "Queue worker",
};

type BadgeTone = "success" | "warning" | "danger";

const READINESS_COPY: Record<ReadinessStatus, { label: string; variant: BadgeTone }> = {
  ready: { label: "Ready", variant: "success" },
  degraded: { label: "Degraded: worker heartbeat missing", variant: "warning" },
  not_ready: { label: "Not ready: a required dependency is down", variant: "danger" },
};

function CheckBadge({ status }: { status: CheckStatus }) {
  switch (status) {
    case "ok":
      return (
        <Badge variant="success">
          <CheckCircle2 aria-hidden="true" /> OK
        </Badge>
      );
    case "unavailable":
      return (
        <Badge variant="warning">
          <AlertTriangle aria-hidden="true" /> Unavailable
        </Badge>
      );
    case "error":
      return (
        <Badge variant="danger">
          <XCircle aria-hidden="true" /> Error
        </Badge>
      );
  }
}

function CheckRow({ check }: { check: CheckData }) {
  return (
    <li className="flex items-start justify-between gap-4 py-3">
      <div>
        <div className="text-sm font-medium">{CHECK_LABELS[check.name] ?? check.name}</div>
        {check.detail ? <div className="text-muted-foreground text-xs">{check.detail}</div> : null}
      </div>
      <div className="flex items-center gap-3">
        <span className="text-muted-foreground text-xs tabular-nums">{check.latency_ms} ms</span>
        <CheckBadge status={check.status} />
      </div>
    </li>
  );
}

export function SystemStatusPage() {
  const { state, isRefreshing, refresh } = useSystemStatus();

  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="text-muted-foreground text-sm">
            System status. Provider configuration arrives in Phase 4.
          </p>
        </div>
        <Button variant="outline" onClick={() => void refresh()} disabled={isRefreshing}>
          <RefreshCw className={isRefreshing ? "animate-spin" : undefined} aria-hidden="true" />
          {isRefreshing ? "Refreshing" : "Refresh"}
        </Button>
      </div>

      {state.phase === "loading" ? (
        <Card role="status" aria-live="polite">
          <CardHeader>
            <CardTitle>Loading system status</CardTitle>
            <CardDescription>Contacting the API.</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      {state.phase === "error" ? (
        <Card role="alert" className="border-danger/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <XCircle className="text-danger size-4" aria-hidden="true" />
              Could not load system status
            </CardTitle>
            <CardDescription>{state.error.message}</CardDescription>
          </CardHeader>
          <CardContent className="text-muted-foreground text-xs">
            Code: <code>{state.error.code}</code>
            {state.error.requestId ? (
              <>
                {" "}
                / Request ID: <code>{state.error.requestId}</code>
              </>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {state.phase === "loaded" ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                API
                <Badge variant="success">
                  <CheckCircle2 aria-hidden="true" /> {state.health.status.toUpperCase()}
                </Badge>
              </CardTitle>
              <CardDescription>
                Version {state.health.version}, environment {state.health.app_env}, checked at{" "}
                {state.fetchedAt.toLocaleTimeString()}
              </CardDescription>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                Dependencies
                <Badge variant={READINESS_COPY[state.readiness.status].variant}>
                  {state.readiness.status === "ready" ? (
                    <CheckCircle2 aria-hidden="true" />
                  ) : (
                    <HelpCircle aria-hidden="true" />
                  )}
                  {READINESS_COPY[state.readiness.status].label}
                </Badge>
              </CardTitle>
              <CardDescription>Probed by the API on every request to /ready.</CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="divide-y">
                {state.readiness.checks.map((check) => (
                  <CheckRow key={check.name} check={check} />
                ))}
              </ul>
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}
