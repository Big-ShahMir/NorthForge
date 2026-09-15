import { Construction } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

interface PlannedAreaPageProps {
  title: string;
  phase: number;
  description: string;
}

/** Visibly labelled stub for product areas that later phases implement. */
export function PlannedAreaPage({ title, phase, description }: PlannedAreaPageProps) {
  return (
    <div className="max-w-2xl space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Construction className="text-warning size-4" aria-hidden="true" />
            Not yet implemented
          </CardTitle>
          <CardDescription>
            This area is scheduled for Phase {phase} of the implementation plan. {description}
          </CardDescription>
        </CardHeader>
        <CardContent className="text-muted-foreground text-sm">
          Track progress in <code>docs/IMPLEMENTATION_PLAN.md</code>.
        </CardContent>
      </Card>
    </div>
  );
}
