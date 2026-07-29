"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";

import { useDecisionLatency, useQueueStats, type DateWindow } from "./use-reporting";

/**
 * Two headline numbers, not a chart — a single value has no shape to plot, and
 * a two-bar chart would be a worse stat tile.
 */
function Tile({
  label,
  caption,
  isPending,
  isError,
  onRetry,
  children,
}: {
  label: string;
  caption: string;
  isPending: boolean;
  isError: boolean;
  onRetry: () => void;
  children: ReactNode;
}) {
  return (
    <div className="border-border bg-card flex flex-col justify-between rounded-lg border px-4 py-3">
      <p className="text-muted-foreground text-[10px] font-semibold tracking-widest uppercase">
        {label}
      </p>

      <div className="mt-3 mb-2">
        {isPending ? (
          <Skeleton className="h-[28px] w-24" />
        ) : isError ? (
          <button
            type="button"
            onClick={onRetry}
            className="text-status-rejected-fg focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-sm text-[12px] font-medium focus-visible:ring-1 focus-visible:outline-none"
          >
            <AlertTriangle size={13} strokeWidth={2} aria-hidden />
            Unavailable
            <RefreshCw size={11} strokeWidth={2} aria-hidden />
          </button>
        ) : (
          children
        )}
      </div>

      <p className="text-muted-foreground text-[11px] leading-snug">{caption}</p>
    </div>
  );
}

function Value({ children, unit }: { children: ReactNode; unit?: string }) {
  return (
    <p className="flex items-baseline gap-1">
      <span className="font-mono text-[26px] leading-none font-semibold tabular-nums">
        {children}
      </span>
      {unit && <span className="text-muted-foreground text-[12px]">{unit}</span>}
    </p>
  );
}

export function QueueHealth({ window }: { window: DateWindow }) {
  const latency = useDecisionLatency(window);
  const stats = useQueueStats();

  const measured = latency.data?.measured ?? 0;
  const median = latency.data?.median_hours;

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Tile
        label="Median time to decision"
        caption={
          measured === 0
            ? "No decision in this range could be timed."
            : `Across ${measured} of ${latency.data?.count ?? 0} decisions in range.`
        }
        isPending={latency.isPending}
        isError={latency.isError}
        onRetry={() => latency.refetch()}
      >
        {/* `measured` and `count` differ when a decision was made without a
            prior notification. Showing a bare "—" without saying so would read
            as "no decisions" rather than "not measurable". */}
        {median === null || median === undefined ? (
          <Value>—</Value>
        ) : (
          <Value unit="hours">{median}</Value>
        )}
      </Tile>

      <Tile
        label="Aging past threshold"
        // Not affected by the date filter: this is a present-tense fact about
        // the queue, and windowing it would produce a number nobody can act on.
        caption={
          stats.data
            ? `Pending longer than ${stats.data.aging_threshold_hours}h, of ${stats.data.pending} awaiting review. Current state — not filtered by date.`
            : "Current state — not filtered by date."
        }
        isPending={stats.isPending}
        isError={stats.isError}
        onRetry={() => stats.refetch()}
      >
        <p className="flex items-baseline gap-1">
          <span
            className={`font-mono text-[26px] leading-none font-semibold tabular-nums ${
              (stats.data?.aging ?? 0) > 0 ? "text-age-overdue" : ""
            }`}
          >
            {stats.data?.aging ?? 0}
          </span>
          <span className="text-muted-foreground text-[12px]">
            {stats.data?.aging === 1 ? "opportunity" : "opportunities"}
          </span>
        </p>
      </Tile>
    </div>
  );
}
