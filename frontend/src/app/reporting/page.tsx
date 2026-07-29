import type { Metadata } from "next";
import { Suspense } from "react";

import { ReportingView } from "@/components/reporting/reporting-view";

export const metadata: Metadata = {
  title: "Reporting · Rox",
  description: "Pipeline funnel, score calibration, and queue health",
};

export default function ReportingPage() {
  return (
    <main className="flex min-h-full flex-1 flex-col">
      {/* `?range=` is read via `useSearchParams`, which client-renders
          everything up to the nearest boundary without this. */}
      <Suspense fallback={null}>
        <ReportingView />
      </Suspense>
    </main>
  );
}
