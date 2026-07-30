"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type ResponseOf } from "@/api/client";

/**
 * One `/reporting/overview` call for the whole page.
 *
 * This replaces the previous one-query-per-chart wiring, by explicit request:
 * one query, one loading state, one error state. What survives from the old
 * design is the per-panel *empty* state — each section still decides for
 * itself what "nothing here" looks like, so one section having no data cannot
 * blank the page.
 */

export interface DateWindow {
  start?: string;
  end?: string;
}

/** Empty strings would reach the API as `?start=`, which fails datetime parsing. */
function windowParams(window: DateWindow) {
  return {
    ...(window.start ? { start: window.start } : {}),
    ...(window.end ? { end: window.end } : {}),
  };
}

export type Overview = ResponseOf<"/reporting/overview", "get">;
export type RunHealth = ResponseOf<"/reporting/run-health", "get">;

/**
 * Below this many decisions a rate is de-emphasized wherever it renders.
 * Read from the data at render, never hardcoded into a component's markup.
 */
export const LOW_N = 5;

export function useOverview(window: DateWindow) {
  return useQuery<Overview>({
    queryKey: ["reporting", "overview", window],
    // No `buckets` param: the design reads the four fixed score bands, and
    // deciles at n=11 would put one decision per bar.
    queryFn: ({ signal }) =>
      api.get("/reporting/overview", { query: windowParams(window), signal }),
  });
}

/**
 * Deliberately *not* windowed, and deliberately not read from the overview
 * response. The overview windows `run_health` along with everything else, but
 * automation health answers "is the machinery working" — a fact about the
 * system, not about the reporting period. Its panel is badged accordingly.
 */
export function useRunHealth() {
  return useQuery<RunHealth>({
    queryKey: ["reporting", "run-health"],
    queryFn: ({ signal }) => api.get("/reporting/run-health", { signal }),
    // Live while a cycle is in flight: the top row is the running run, and
    // its ticking duration is the only mid-run progress the UI has. Stops
    // by itself when the run reaches a terminal status.
    refetchInterval: (query) =>
      query.state.data?.recent_runs?.[0]?.status === "running" ? 5_000 : false,
  });
}

/**
 * Start a forced research cycle, detached server-side (`background=true`).
 *
 * Detached is not a nicety: a blocking request is cancelled if the client
 * disconnects — a page reload mid-run would kill the cycle. The server owns
 * the run; this hook only starts it and watches run-health.
 */
export function useTriggerRun() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () =>
      api.post("/admin/research/run", {
        query: {
          background: true,
          force_extract: true,
          ignore_cooldown: true,
        },
      }),
    onSuccess: () => {
      // The running row appears on the next run-health fetch, which also
      // switches that query into its polling mode.
      queryClient.invalidateQueries({ queryKey: ["reporting", "run-health"] });
      // The run ends with new opportunities and a digest; stale queue counts
      // would misstate what just happened. Polling run-health covers the
      // transition; these cover the end state whenever the user navigates.
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      queryClient.invalidateQueries({ queryKey: ["reporting", "overview"] });
    },
  });
}
