import { NavLink, Outlet } from "react-router";
import { FolderKanban, GitBranch, Play, FlaskConical, Settings } from "lucide-react";

import { cn } from "@/lib/utils";
import { SystemStatusBadge } from "@/components/layout/SystemStatusBadge";

const NAV_ITEMS = [
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/workflows", label: "Workflows", icon: GitBranch },
  { to: "/runs", label: "Runs", icon: Play },
  { to: "/evaluations", label: "Evaluations", icon: FlaskConical },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

export function AppShell() {
  return (
    <div className="flex min-h-screen">
      <aside className="bg-card flex w-56 shrink-0 flex-col border-r" aria-label="Primary">
        <div className="flex h-14 items-center border-b px-4">
          <span className="text-sm font-semibold tracking-tight">NorthForge</span>
        </div>
        <nav className="flex flex-col gap-1 p-2">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "text-muted-foreground hover:bg-accent hover:text-accent-foreground flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium",
                  isActive && "bg-accent text-accent-foreground",
                )
              }
            >
              <Icon className="size-4" aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="bg-card flex h-14 items-center justify-between border-b px-6">
          <span className="text-muted-foreground text-sm">No project selected</span>
          <SystemStatusBadge />
        </header>
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
