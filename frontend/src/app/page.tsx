import type { Metadata } from "next";
import { Suspense } from "react";

import { PipelineView } from "@/components/pipeline/pipeline-view";
import { TableSkeleton } from "@/components/pipeline/table-skeleton";

export const metadata: Metadata = {
  title: "Pipeline · Rox",
  description: "Scored opportunities awaiting review",
};

/**
 * Server component shell. The pipeline itself filters, sorts, and pages, so it
 * lives on the client with TanStack Query — refetching the whole route on every
 * filter change would be strictly worse here.
 */
export default function PipelinePage() {
  return (
    <main className="flex min-h-full flex-1 flex-col">
      {/* Required: `PipelineView` reads the filters from `useSearchParams`,
          which would otherwise force the whole tree above it to be
          client-rendered instead of prerendered. */}
      <Suspense fallback={<TableSkeleton />}>
        <PipelineView />
      </Suspense>
    </main>
  );
}
