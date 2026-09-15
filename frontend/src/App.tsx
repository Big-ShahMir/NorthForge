import { Navigate, Route, Routes } from "react-router";

import { AppShell } from "@/components/layout/AppShell";
import { SystemStatusProvider } from "@/lib/system-status";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { PlannedAreaPage } from "@/pages/PlannedAreaPage";
import { SystemStatusPage } from "@/pages/SystemStatusPage";

const PLANNED_AREAS = [
  {
    path: "projects",
    title: "Projects",
    phase: 7,
    description: "Project dashboards need the Phase 1 data model and the Phase 7 interface.",
  },
  {
    path: "workflows",
    title: "Workflows",
    phase: 7,
    description: "Workflow proposal and editing depend on the Phase 5 planner.",
  },
  {
    path: "runs",
    title: "Runs",
    phase: 7,
    description: "Run supervision depends on the Phase 6 execution graph.",
  },
  {
    path: "evaluations",
    title: "Evaluations",
    phase: 8,
    description: "Evaluation cases and reports arrive with the feedback loop.",
  },
] as const;

export function App() {
  return (
    <SystemStatusProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/settings" replace />} />
          {PLANNED_AREAS.map((area) => (
            <Route
              key={area.path}
              path={area.path}
              element={
                <PlannedAreaPage
                  title={area.title}
                  phase={area.phase}
                  description={area.description}
                />
              }
            />
          ))}
          <Route path="settings" element={<SystemStatusPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </SystemStatusProvider>
  );
}
