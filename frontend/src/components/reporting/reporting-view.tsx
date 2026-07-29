"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { CalibrationChart } from "./calibration-chart";
import {
  DateRangeFilter,
  RANGES,
  rangeToGrouping,
  rangeToWindow,
  type RangeId,
} from "./date-range-filter";
import { FunnelChart } from "./funnel-chart";
import { QueueHealth } from "./queue-health";
import { RejectionChart } from "./rejection-chart";
import { SignalChart } from "./signal-chart";

const DEFAULT_RANGE: RangeId = "30d";

function isRange(value: string | null): value is RangeId {
  return RANGES.some((r) => r.id === value);
}

export function ReportingView() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const rangeParam = searchParams.get("range");
  const range: RangeId = isRange(rangeParam) ? rangeParam : DEFAULT_RANGE;

  // `now` is pinned once at mount, never read during render. Calling
  // `Date.now()` inline would give every chart a slightly different window and
  // change the query key on each render, refetching forever — and it is an
  // impure read during render, which the compiler rejects outright.
  const [mountedAt] = useState(() => Date.now());
  const window = useMemo(() => rangeToWindow(range, mountedAt), [range, mountedAt]);
  const groupBy = rangeToGrouping(range);

  const selectRange = (next: RangeId) => {
    const params = new URLSearchParams(searchParams.toString());
    if (next === DEFAULT_RANGE) params.delete("range");
    else params.set("range", next);
    const query = params.toString();
    router.push(query ? `/reporting?${query}` : "/reporting", { scroll: false });
  };

  return (
    <div className="flex min-h-full flex-col">
      <header className="border-border bg-card flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
        <div>
          <h1 className="text-[15px] leading-none font-semibold">Reporting</h1>
          <p className="text-muted-foreground mt-1 text-[12px] leading-none">
            Whether the pipeline is working, and where it isn&rsquo;t
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

      <div className="flex flex-col gap-4 p-6">
        <QueueHealth window={window} />

        {/* Priority order: the funnel and calibration answer the two questions
            worth asking first, so they get the full width. */}
        <FunnelChart window={window} />
        <CalibrationChart window={window} />

        <div className="grid gap-4 xl:grid-cols-2">
          <RejectionChart window={window} groupBy={groupBy} />
          <SignalChart window={window} />
        </div>
      </div>
    </div>
  );
}
