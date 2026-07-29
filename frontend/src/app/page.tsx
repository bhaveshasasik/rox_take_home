import type { Metadata } from "next";

import { PipelineView } from "@/components/pipeline/pipeline-view";

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
      <PipelineView />
    </main>
  );
}
