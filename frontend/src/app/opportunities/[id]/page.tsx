import type { Metadata } from "next";

import { OpportunityDetailView } from "@/components/opportunity/detail-view";

export const metadata: Metadata = {
  title: "Opportunity · Rox",
};

/**
 * `params` is a Promise in Next 16 — synchronous access was removed, not just
 * deprecated. See docs/01-app/02-guides/upgrading/version-16.md.
 */
export default async function OpportunityPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <main className="flex min-h-full flex-1 flex-col">
      <OpportunityDetailView id={id} />
    </main>
  );
}
