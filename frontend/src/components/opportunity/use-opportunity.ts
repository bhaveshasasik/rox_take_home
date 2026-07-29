"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type ResponseOf, type Schemas } from "@/api/client";

export type OpportunityDetail = Schemas["OpportunityDetailOut"];
export type ResearchSignal = Schemas["ResearchSignalOut"];
export type ExtractedSignal = Schemas["ExtractedSignalOut"];
export type SourceRef = Schemas["SourceRefOut"];

/** The spec's enum is the whole vocabulary — no invented reasons. */
export const REASON_CODES = [
  "good_fit",
  "bad_timing",
  "wrong_persona",
  "already_engaged",
  "low_signal",
  "other",
] as const satisfies readonly Schemas["ReasonCode"][];

export const REASON_LABELS: Record<Schemas["ReasonCode"], string> = {
  good_fit: "Good fit",
  bad_timing: "Bad timing",
  wrong_persona: "Wrong persona",
  already_engaged: "Already engaged",
  low_signal: "Low signal",
  other: "Other",
};

export function useOpportunity(id: string) {
  return useQuery<ResponseOf<"/opportunities/{opportunity_id}", "get">>({
    queryKey: ["opportunity", id],
    queryFn: ({ signal }) =>
      api.get("/opportunities/{opportunity_id}", { path: { opportunity_id: id }, signal }),
  });
}

export interface DecisionInput {
  decision: Schemas["DecisionType"];
  reason_code?: Schemas["ReasonCode"] | null;
  notes?: string | null;
}

export function useDecide(id: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: DecisionInput) =>
      api.post("/opportunities/{opportunity_id}/decision", {
        path: { opportunity_id: id },
        body: input,
      }),
    onSuccess: (updated) => {
      // The response is the updated detail, so seed it rather than refetching.
      queryClient.setQueryData(["opportunity", id], updated);
      // Every list query, whatever its filters — the row's status just changed,
      // and under the default "pending review" filter it should drop out.
      // `stats` shares this prefix, so the header counts refresh too.
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
    },
  });
}
