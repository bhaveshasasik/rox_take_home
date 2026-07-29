import type { Schemas } from "@/api/client";
import {
  DEFAULT_FILTERS,
  type OpportunityFilters,
} from "@/components/pipeline/use-opportunities";

/**
 * Filters live in the URL rather than component state, so the view is
 * shareable and the back button restores it.
 *
 * The URL is the single source of truth — there is no `useState` mirror to
 * drift out of sync, and browser history navigation works for free.
 */

const STATUSES: readonly Schemas["OpportunityStatus"][] = [
  "new",
  "accepted",
  "rejected",
  "superseded",
];

function isStatus(value: string): value is Schemas["OpportunityStatus"] {
  return (STATUSES as readonly string[]).includes(value);
}

export function parseFilters(params: URLSearchParams): OpportunityFilters {
  // Unknown values are dropped rather than passed through: a hand-edited
  // `?status=bogus` should not reach the API and 422.
  const status = params.getAll("status").filter(isStatus);
  const signalType = params.getAll("signal_type").filter(Boolean);
  const minScore = Number(params.get("min_score"));
  const limit = Number(params.get("limit"));
  const offset = Number(params.get("offset"));
  const order = params.get("order");

  return {
    status: status.length ? status : undefined,
    signal_type: signalType.length ? signalType : undefined,
    min_score: Number.isFinite(minScore) && params.has("min_score") ? minScore : undefined,
    sort: params.get("sort") ?? DEFAULT_FILTERS.sort,
    order: order === "asc" || order === "desc" ? order : DEFAULT_FILTERS.order,
    limit: Number.isFinite(limit) && limit > 0 ? limit : DEFAULT_FILTERS.limit,
    offset: Number.isFinite(offset) && offset > 0 ? offset : 0,
  };
}

export function serializeFilters(filters: OpportunityFilters): string {
  const params = new URLSearchParams();

  for (const value of filters.status ?? []) params.append("status", value);
  for (const value of filters.signal_type ?? []) params.append("signal_type", value);
  if (filters.min_score !== undefined) params.set("min_score", String(filters.min_score));

  // Sort and order are always written. They can never be "cleared", and their
  // presence guarantees the query string is non-empty after any user action —
  // which is what keeps `clear filters` from looking like a first visit and
  // being re-normalised back to the defaults.
  params.set("sort", filters.sort ?? DEFAULT_FILTERS.sort!);
  params.set("order", filters.order ?? DEFAULT_FILTERS.order!);

  if (filters.limit && filters.limit !== DEFAULT_FILTERS.limit) {
    params.set("limit", String(filters.limit));
  }
  if (filters.offset) params.set("offset", String(filters.offset));

  return params.toString();
}

/** What a bare `/` normalises to: the actionable queue, highest score first. */
export const DEFAULT_QUERY_STRING = serializeFilters(DEFAULT_FILTERS);
