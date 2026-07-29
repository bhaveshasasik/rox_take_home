"use client";

import { useQuery } from "@tanstack/react-query";

import { api, type ResponseOf } from "@/api/client";
import { AGING_THRESHOLD_HOURS } from "@/lib/pipeline";

/**
 * One query per chart, deliberately — not a single `/reporting/overview` call.
 *
 * The overview endpoint would couple every chart's fate to one request: a
 * failure anywhere blanks the whole page, which is exactly what we were asked
 * to avoid. Separate queries let each card own its loading, empty, and error
 * state, and they run in parallel anyway.
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

export function useFunnel(window: DateWindow) {
  return useQuery<ResponseOf<"/reporting/funnel", "get">>({
    queryKey: ["reporting", "funnel", window],
    queryFn: ({ signal }) =>
      api.get("/reporting/funnel", { query: windowParams(window), signal }),
  });
}

export function useScoreCalibration(window: DateWindow) {
  return useQuery<ResponseOf<"/reporting/score-calibration", "get">>({
    queryKey: ["reporting", "score-calibration", window],
    queryFn: ({ signal }) =>
      // deciles: the whole question is whether the score is predictive, and
      // four coarse bands cannot show a trend
      api.get("/reporting/score-calibration", {
        query: { ...windowParams(window), buckets: 10 },
        signal,
      }),
  });
}

export function useRejectionReasons(window: DateWindow, groupBy: "day" | "week") {
  return useQuery<ResponseOf<"/reporting/rejection-reasons", "get">>({
    queryKey: ["reporting", "rejection-reasons", window, groupBy],
    queryFn: ({ signal }) =>
      api.get("/reporting/rejection-reasons", {
        query: { ...windowParams(window), group_by: groupBy },
        signal,
      }),
  });
}

export function useSignalPerformance(window: DateWindow) {
  return useQuery<ResponseOf<"/reporting/signal-performance", "get">>({
    queryKey: ["reporting", "signal-performance", window],
    queryFn: ({ signal }) =>
      api.get("/reporting/signal-performance", { query: windowParams(window), signal }),
  });
}

export function useDecisionLatency(window: DateWindow) {
  return useQuery<ResponseOf<"/reporting/decision-latency", "get">>({
    queryKey: ["reporting", "decision-latency", window],
    queryFn: ({ signal }) =>
      api.get("/reporting/decision-latency", { query: windowParams(window), signal }),
  });
}

/**
 * Deliberately *not* windowed. "How many are aging right now" is a present-tense
 * fact about the queue; filtering it to a past window would produce a number
 * nobody can act on. The card labels itself as current-state.
 */
export function useQueueStats() {
  return useQuery<ResponseOf<"/opportunities/stats", "get">>({
    queryKey: ["opportunities", "stats", AGING_THRESHOLD_HOURS],
    queryFn: ({ signal }) =>
      api.get("/opportunities/stats", {
        query: { aging_hours: AGING_THRESHOLD_HOURS },
        signal,
      }),
  });
}
