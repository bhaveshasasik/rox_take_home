"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError } from "@/api/client";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * One card per chart, owning its own loading / empty / error state.
 *
 * Each chart has its own query, so a failing endpoint degrades to a retry
 * button inside its own card and every other chart still renders.
 */
export function ChartCard({
  title,
  caption,
  isPending,
  isError,
  error,
  onRetry,
  isRetrying,
  isEmpty,
  emptyMessage,
  action,
  height = 260,
  children,
}: {
  title: string;
  /** What the chart tells you, not what it plots. */
  caption: string;
  isPending: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  isRetrying?: boolean;
  isEmpty?: boolean;
  emptyMessage?: string;
  action?: ReactNode;
  height?: number;
  children: ReactNode;
}) {
  return (
    <section className="border-border bg-card flex flex-col rounded-lg border">
      <header className="border-border flex items-start justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-[13px] leading-none font-semibold">{title}</h2>
          <p className="text-muted-foreground mt-1.5 text-[11px] leading-snug">{caption}</p>
        </div>
        {action}
      </header>

      <div className="flex-1 p-4" style={{ minHeight: height }}>
        {isPending ? (
          <ChartSkeleton height={height} />
        ) : isError ? (
          <ChartError error={error} onRetry={onRetry} isRetrying={isRetrying} height={height} />
        ) : isEmpty ? (
          <ChartEmpty message={emptyMessage} height={height} />
        ) : (
          children
        )}
      </div>
    </section>
  );
}

/** Bars of varying height, so the shape reads as a chart rather than a block. */
function ChartSkeleton({ height }: { height: number }) {
  const bars = [55, 80, 40, 95, 65, 75, 35];
  return (
    <div
      className="flex items-end gap-2"
      style={{ height: height - 32 }}
      aria-busy="true"
      aria-live="polite"
    >
      <span className="sr-only">Loading chart…</span>
      {bars.map((pct, index) => (
        <Skeleton key={index} className="flex-1 rounded-sm" style={{ height: `${pct}%` }} />
      ))}
    </div>
  );
}

function ChartEmpty({ message, height }: { message?: string; height: number }) {
  return (
    <div
      className="text-muted-foreground flex flex-col items-center justify-center text-center"
      style={{ height: height - 32 }}
    >
      <p className="text-[12px]">{message ?? "No data in this range."}</p>
      <p className="mt-1 text-[11px]">Try widening the date range.</p>
    </div>
  );
}

function ChartError({
  error,
  onRetry,
  isRetrying,
  height,
}: {
  error: unknown;
  onRetry: () => void;
  isRetrying?: boolean;
  height: number;
}) {
  const detail =
    error instanceof ApiError
      ? `HTTP ${error.status}`
      : error instanceof Error
        ? "Couldn't reach the service"
        : "Unknown error";

  return (
    <div
      className="flex flex-col items-center justify-center text-center"
      style={{ height: height - 32 }}
    >
      <AlertTriangle
        size={16}
        strokeWidth={1.5}
        className="text-status-rejected-fg mb-2"
        aria-hidden
      />
      <p className="text-[12px] font-medium">Couldn&rsquo;t load this chart</p>
      <p className="text-muted-foreground mt-0.5 font-mono text-[11px]">{detail}</p>
      <button
        type="button"
        onClick={onRetry}
        disabled={isRetrying}
        className="border-border bg-card hover:bg-accent focus-visible:ring-ring mt-3 inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none disabled:opacity-40"
      >
        <RefreshCw
          size={10}
          strokeWidth={2}
          aria-hidden
          className={isRetrying ? "animate-spin" : undefined}
        />
        Retry
      </button>
    </div>
  );
}
