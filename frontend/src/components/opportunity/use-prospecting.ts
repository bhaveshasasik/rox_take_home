"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api, type ResponseOf, type Schemas } from "@/api/client";

export type Sequence = Schemas["SequenceOut"];
export type Enrollment = Schemas["EnrollmentOut"];
export type OutreachEmail = Schemas["OutreachEmailOut"];

/** How often to re-check while prospecting is still running. */
const POLL_INTERVAL_MS = 3_000;

/**
 * Give up polling after this many tries (~60s).
 *
 * Prospecting is a fire-and-forget background task started on accept. If it
 * throws before creating a Sequence row, the endpoint 404s forever — polling
 * without a ceiling would leave a spinner on screen for the rest of the
 * session. On expiry the UI offers a manual re-check instead.
 */
const MAX_POLLS = 20;

/** A 404 here means "prospecting hasn't produced anything yet", not a failure. */
export function isNotStarted(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

export function useProspecting(opportunityId: string, enabled: boolean) {
  return useQuery<ResponseOf<"/prospecting/opportunities/{opportunity_id}", "get">>({
    queryKey: ["prospecting", opportunityId],
    queryFn: ({ signal }) =>
      api.get("/prospecting/opportunities/{opportunity_id}", {
        path: { opportunity_id: opportunityId },
        signal,
      }),
    enabled,
    // A 404 is an expected answer while the background task runs, so retrying
    // it as a failure would only add latency before the next poll.
    retry: (_count, error) => !isNotStarted(error) && _count < 2,
    refetchInterval: (query) => {
      // Stop as soon as there is a sequence — success ends the poll, nothing
      // has to remember to switch it off.
      if (query.state.data) return false;
      if (!isNotStarted(query.state.error)) return false;
      return query.state.dataUpdateCount + query.state.errorUpdateCount < MAX_POLLS
        ? POLL_INTERVAL_MS
        : false;
    },
  });
}

/** True once polling has given up, so the UI can offer a manual re-check. */
export function hasStoppedPolling(errorUpdateCount: number): boolean {
  return errorUpdateCount >= MAX_POLLS;
}

/**
 * Manual re-run. Also the retry path for a sequence that failed — the backend
 * retries a FAILED sequence in place rather than refusing as a duplicate.
 */
export function useRerunProspecting(opportunityId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () =>
      api.post("/prospecting/opportunities/{opportunity_id}/run", {
        path: { opportunity_id: opportunityId },
      }),
    onSuccess: (sequence) => {
      queryClient.setQueryData(["prospecting", opportunityId], sequence);
      // `has_sequence` and the stage pill on the detail header both change.
      queryClient.invalidateQueries({ queryKey: ["opportunity", opportunityId] });
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
    },
  });
}
