"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, type ResponseOf, type Schemas } from "@/api/client";
import { AGING_THRESHOLD_HOURS } from "@/lib/pipeline";

export type Opportunity = Schemas["OpportunityOut"];

/** Exactly the params `GET /opportunities` accepts — no invented filters. */
export interface OpportunityFilters {
  status?: Schemas["OpportunityStatus"][];
  signal_type?: string[];
  min_score?: number;
  search?: string;
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

/**
 * The queue opens on what needs action, ranked by how much it's worth —
 * not on an unfiltered list in creation order.
 */
export const DEFAULT_FILTERS: OpportunityFilters = {
  status: ["new"],
  sort: "score",
  order: "desc",
  limit: 50,
  offset: 0,
};

export function useOpportunities(filters: OpportunityFilters) {
  return useQuery<ResponseOf<"/opportunities", "get">>({
    queryKey: ["opportunities", filters],
    queryFn: ({ signal }) => api.get("/opportunities", { query: filters, signal }),
    // Paging and filter changes swap the whole result set; without this the
    // table unmounts to a skeleton on every keystroke and the layout jumps.
    placeholderData: keepPreviousData,
  });
}

/**
 * Queue health for the header. Separate from the list query because it is
 * filter-independent — it describes the whole review queue, so it must not
 * refetch every time the user narrows the table.
 */
export function usePipelineStats() {
  return useQuery<ResponseOf<"/opportunities/stats", "get">>({
    queryKey: ["opportunities", "stats", AGING_THRESHOLD_HOURS],
    queryFn: ({ signal }) =>
      api.get("/opportunities/stats", {
        // the row-level red and the header count must share one threshold,
        // or the table will contradict the number above it
        query: { aging_hours: AGING_THRESHOLD_HOURS },
        signal,
      }),
  });
}
