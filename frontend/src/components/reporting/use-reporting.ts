"use client";

import { useQuery } from "@tanstack/react-query";

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
  });
}
