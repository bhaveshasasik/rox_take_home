import type { Schemas } from "@/api/client";

/** Rows age out of the review queue at this point, and turn red. */
export const AGING_THRESHOLD_HOURS = 48;

/**
 * Where the age label switches from hours to days. Independent of the aging
 * threshold on purpose: they coincide at 48 in the design, but they answer
 * different questions, and tying them means lowering the red threshold starts
 * printing "0d" for rows only a few hours old.
 */
const HOURS_DISPLAY_LIMIT = 48;

export function hoursSince(iso: string, now: number = Date.now()): number {
  // The API serialises UTC with an explicit `Z`, so `Date` parses it
  // unambiguously. It used to send a bare `2026-07-28T06:26:29`, which JS reads
  // as *local* time — every age was wrong by the viewer's offset.
  return (now - new Date(iso).getTime()) / 3_600_000;
}

/**
 * Compact age, matching the design's `2h` / `31h` / `3d` scale.
 *
 * Stays in hours up to the aging threshold so a reviewer can see how close a
 * row is to going overdue; past that the exact hour stops mattering.
 */
export function formatAge(iso: string, now: number = Date.now()): string {
  const hours = hoursSince(iso, now);
  if (hours < 0) return "0m"; // clock skew — never render a negative age
  if (hours < 1) return `${Math.floor(hours * 60)}m`;
  if (hours < HOURS_DISPLAY_LIMIT) return `${Math.floor(hours)}h`;
  // floor can only reach 0 if the limit ever drops below 24h; "0d" is nonsense
  return `${Math.max(1, Math.floor(hours / 24))}d`;
}

export function isAging(iso: string, now: number = Date.now()): boolean {
  return hoursSince(iso, now) >= AGING_THRESHOLD_HOURS;
}

export type PipelineStatus = "Pending" | "Accepted" | "Rejected" | "Prospecting";

/** Stages that mean prospecting has started, whatever `status` still says. */
const PROSPECTING_STAGES: ReadonlySet<Schemas["Stage"]> = new Set([
  "prospected",
  "sequenced",
  "outreach_sent",
]);

/**
 * The design's four-value pill is a composite: three of its values come from
 * `status`, but "Prospecting" is a *stage*. An accepted opportunity that has
 * been sequenced reads as Prospecting, which is the more specific truth.
 */
export function pipelineStatus(
  status: Schemas["OpportunityStatus"],
  stage: Schemas["Stage"],
): PipelineStatus {
  if (PROSPECTING_STAGES.has(stage)) return "Prospecting";
  if (status === "accepted") return "Accepted";
  if (status === "rejected") return "Rejected";
  return "Pending";
}

/** `opportunity_created` -> `Opportunity created`, for the stage subtext. */
export function humanizeStage(stage: Schemas["Stage"]): string {
  const words = stage.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
