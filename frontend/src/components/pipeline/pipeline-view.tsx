"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo } from "react";

import { DEFAULT_QUERY_STRING, parseFilters, serializeFilters } from "@/lib/url-state";

import { EmptyState } from "./empty-state";
import { ErrorState } from "./error-state";
import { FilterBar } from "./filter-bar";
import { PipelineTable, type SortState } from "./pipeline-table";
import { TableSkeleton } from "./table-skeleton";
import {
  DEFAULT_FILTERS,
  useOpportunities,
  usePipelineStats,
  type OpportunityFilters,
} from "./use-opportunities";

export function PipelineView() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const search = searchParams.toString();
  // A bare `/` means "first visit", not "no filters" — show the default queue
  // immediately and normalise the URL to match, so the next interaction has
  // something explicit to diff against.
  const isFirstVisit = search === "";
  const filters = useMemo(
    () => (isFirstVisit ? DEFAULT_FILTERS : parseFilters(new URLSearchParams(search))),
    [isFirstVisit, search],
  );

  useEffect(() => {
    if (isFirstVisit) router.replace(`/?${DEFAULT_QUERY_STRING}`, { scroll: false });
  }, [isFirstVisit, router]);

  const query = useOpportunities(filters);
  const stats = usePipelineStats();

  // `push`, not `replace`: filter changes are discrete user actions, so the
  // back button should undo them.
  const update = (next: Partial<OpportunityFilters>) =>
    router.push(`/?${serializeFilters({ ...filters, ...next })}`, { scroll: false });

  // Stable identity: `?? []` would allocate a fresh array every render, which
  // defeats the memo below and re-renders the table on every parent render.
  const rows = useMemo(() => query.data?.items ?? [], [query.data]);
  const total = query.data?.total ?? 0;

  // Derived from the loaded page, not fetched — the API has no endpoint that
  // enumerates signal types, and these rows are already in hand.
  const signalOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of rows) seen.set(row.signal_type, row.signal_label ?? row.signal_type);
    return [...seen]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [rows]);

  const sortState: SortState = {
    sort: filters.sort ?? "created_at",
    order: filters.order ?? "desc",
  };

  const isFiltered =
    filters.status !== undefined ||
    filters.min_score !== undefined ||
    filters.signal_type !== undefined;

  const limit = filters.limit ?? DEFAULT_FILTERS.limit!;
  const offset = filters.offset ?? 0;
  const hasPages = total > limit;

  return (
    <div className="flex min-h-full flex-col">
      <header className="border-border bg-card border-b px-6 py-4">
        <h1 className="text-[15px] leading-none font-semibold">Pipeline</h1>
        <p className="text-muted-foreground mt-1 text-[12px] leading-none">
          {stats.isPending ? (
            "Loading…"
          ) : stats.isError ? (
            // the list has its own error state; don't repeat the failure here
            "—"
          ) : (
            <>
              {stats.data.pending} pending review
              {stats.data.aging > 0 && (
                <>
                  <span className="text-border mx-1.5">·</span>
                  <span className="text-age-overdue font-medium">
                    {stats.data.aging} aging past {stats.data.aging_threshold_hours}h
                  </span>
                </>
              )}
            </>
          )}
        </p>
      </header>

      <FilterBar filters={filters} signalOptions={signalOptions} onChange={update} />

      {query.isError ? (
        <ErrorState
          error={query.error}
          onRetry={() => query.refetch()}
          isRetrying={query.isFetching}
        />
      ) : query.isPending ? (
        <TableSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState filtered={isFiltered} onClearFilters={() => router.push(`/?${serializeFilters({ limit })}`, { scroll: false })} />
      ) : (
        <PipelineTable
          rows={rows}
          sortState={sortState}
          onSortChange={(next) => update({ ...next, offset: 0 })}
        />
      )}

      {!query.isError && (
        <div className="border-border bg-background text-muted-foreground flex items-center justify-between border-t px-6 py-3 text-[11px]">
          <span>
            {query.isPending
              ? "…"
              : hasPages
                ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total} opportunities`
                : `${total} ${total === 1 ? "opportunity" : "opportunities"}`}
          </span>

          {hasPages && (
            <div className="flex items-center gap-1">
              <PageButton
                label="Previous"
                disabled={offset === 0}
                onClick={() => update({ offset: Math.max(0, offset - limit) })}
              />
              <PageButton
                label="Next"
                disabled={offset + limit >= total}
                onClick={() => update({ offset: offset + limit })}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PageButton({
  label,
  disabled,
  onClick,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="border-border bg-card hover:bg-accent focus-visible:ring-ring rounded-md border px-2 py-1 font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none disabled:opacity-40"
    >
      {label}
    </button>
  );
}
