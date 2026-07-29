"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ErrorState } from "@/components/pipeline/error-state";
import { Skeleton } from "@/components/ui/skeleton";

import { CalibrationChart } from "./calibration-chart";
import { CoveragePanel } from "./coverage-panel";
import {
  DateRangeFilter,
  RANGES,
  rangeToWindow,
  type RangeId,
} from "./date-range-filter";
import { FunnelChart } from "./funnel-chart";
import { HeadlineStrip } from "./headline-strip";
import { LatencyPanel } from "./latency-panel";
import { RejectionChart } from "./rejection-chart";
import { RunHealthPanel } from "./run-health-panel";
import { SignalChart } from "./signal-chart";
import { YieldPanel } from "./yield-panel";
import { useOverview } from "./use-reporting";

const DEFAULT_RANGE: RangeId = "30d";

function isRange(value: string | null): value is RangeId {
  return RANGES.some((r) => r.id === value);
}

/**
 * One `/reporting/overview` query feeds every section except automation
 * health, which runs its own unwindowed query — the date filter describes the
 * reporting period, and run reliability is not a fact about the period.
 *
 * Loading and error are page-level because the data is one request; empty is
 * per-panel because "no rejections" and "no decisions" are different facts
 * and neither should blank the page.
 */
export function ReportingView() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const rangeParam = searchParams.get("range");
  const range: RangeId = isRange(rangeParam) ? rangeParam : DEFAULT_RANGE;

  // `now` is pinned once at mount, never read during render. Calling
  // `Date.now()` inline would change the query key on each render, refetching
  // forever — and it is an impure read during render.
  const [mountedAt] = useState(() => Date.now());
  const window = useMemo(() => rangeToWindow(range, mountedAt), [range, mountedAt]);

  const query = useOverview(window);

  const selectRange = (next: RangeId) => {
    const params = new URLSearchParams(searchParams.toString());
    if (next === DEFAULT_RANGE) params.delete("range");
    else params.set("range", next);
    const queryString = params.toString();
    router.push(queryString ? `/reporting?${queryString}` : "/reporting", { scroll: false });
  };

  const overview = query.data;
  const sent =
    overview?.funnel.steps.find((step) => step.stage === "outreach_sent")?.count ?? 0;

  return (
    <div className="flex min-h-full flex-col">
      <header className="border-border bg-card flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
        <div>
          <h1 className="text-[15px] leading-none font-semibold">Reporting</h1>
          <p className="text-muted-foreground mt-1 text-[12px] leading-none">
            {overview
              ? `Pipeline performance · ${overview.account_coverage.total_accounts} accounts`
              : "Pipeline performance"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <DateRangeFilter value={range} onChange={selectRange} />
          <Link
            href="/"
            className="border-border bg-card hover:bg-accent focus-visible:ring-ring rounded-md border px-3 py-1.5 text-[12px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none"
          >
            Pipeline
          </Link>
        </div>
      </header>

      {query.isPending ? (
        <PageSkeleton />
      ) : query.isError ? (
        <ErrorState
          error={query.error}
          onRetry={() => query.refetch()}
          isRetrying={query.isFetching}
          title="Failed to load reporting"
        />
      ) : (
        <>
          <HeadlineStrip headline={overview!.headline} />

          <div className="flex flex-col gap-4 p-6">
            {/* Priority order: the funnel and calibration answer the two
                questions worth asking first, so they get the full width. */}
            <FunnelChart
              funnel={overview!.funnel}
              drafted={overview!.prospecting_yield.total_emails_drafted}
              onRetry={() => query.refetch()}
            />
            <CalibrationChart
              calibration={overview!.score_calibration}
              onRetry={() => query.refetch()}
            />
            <SignalChart
              rows={overview!.signal_performance}
              onRetry={() => query.refetch()}
            />

            <div className="grid gap-4 xl:grid-cols-2">
              <LatencyPanel
                latency={overview!.decision_latency}
                onRetry={() => query.refetch()}
              />
              <RejectionChart
                rejections={overview!.rejection_reasons}
                onRetry={() => query.refetch()}
              />
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <YieldPanel
                prospecting={overview!.prospecting_yield}
                sent={sent}
                onRetry={() => query.refetch()}
              />
              <CoveragePanel
                coverage={overview!.account_coverage}
                onRetry={() => query.refetch()}
              />
            </div>

            {/* Own query, own window — see RunHealthPanel. Rendered outside
                the overview branch's data so its loading and error states are
                independent of the page's. */}
            <RunHealthPanel />
          </div>
        </>
      )}
    </div>
  );
}

/** Mirrors the loaded layout so nothing jumps when data arrives. */
function PageSkeleton() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading reporting…</span>
      <div className="border-border bg-card border-b px-6 py-4">
        <div className="flex gap-6">
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i}>
              <Skeleton className="h-6 w-12" />
              <Skeleton className="mt-1.5 h-3 w-20" />
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-4 p-6">
        <Skeleton className="h-[320px] rounded-lg" />
        <Skeleton className="h-[320px] rounded-lg" />
        <div className="grid gap-4 xl:grid-cols-2">
          <Skeleton className="h-[240px] rounded-lg" />
          <Skeleton className="h-[240px] rounded-lg" />
        </div>
      </div>
    </div>
  );
}
