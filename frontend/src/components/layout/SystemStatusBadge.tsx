import { AlertTriangle, CheckCircle2, Loader2, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useSystemStatus } from "@/lib/system-status";

export function SystemStatusBadge() {
  const { state } = useSystemStatus();

  if (state.phase === "loading") {
    return (
      <Badge variant="neutral" aria-live="polite">
        <Loader2 className="animate-spin" aria-hidden="true" /> Checking system
      </Badge>
    );
  }
  if (state.phase === "error") {
    return (
      <Badge variant="danger" aria-live="polite">
        <XCircle aria-hidden="true" /> API unreachable
      </Badge>
    );
  }
  switch (state.readiness.status) {
    case "ready":
      return (
        <Badge variant="success" aria-live="polite">
          <CheckCircle2 aria-hidden="true" /> System ready
        </Badge>
      );
    case "degraded":
      return (
        <Badge variant="warning" aria-live="polite">
          <AlertTriangle aria-hidden="true" /> Worker offline
        </Badge>
      );
    case "not_ready":
      return (
        <Badge variant="danger" aria-live="polite">
          <XCircle aria-hidden="true" /> Dependencies down
        </Badge>
      );
  }
}
