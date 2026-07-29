"use client";

import { useState } from "react";

import type { Schemas } from "@/api/client";
import { TABLE_CELL, TABLE_HEAD, TABLE_ROW } from "@/components/pipeline/table-chrome";
import { formatAge } from "@/lib/pipeline";
import { cn } from "@/lib/utils";

import { ChartCard } from "./chart-card";
import { Stat } from "./stat";
import { useRunHealth } from "./use-reporting";

/**
 * A run reporting `running` for longer than this is presumed stuck. There is
 * no API field for it — the research cycle is scheduled every 15 minutes and
 * a healthy run finishes in single-digit minutes, so an hour of `running` is
 * a hang, not work.
 */
const STUCK_AFTER_MS = 60 * 60 * 1000;

type RunStatus = Schemas["RunStatus"];

/** `stuck` is derived, never returned — see STUCK_AFTER_MS. */
type DisplayStatus = RunStatus | "stuck";

const STATUS_BADGES: Record<DisplayStatus, { label: string; cls: string }> = {
  succeeded: { label: "Success", cls: "bg-status-accepted text-status-accepted-fg" },
  failed: { label: "Error", cls: "bg-status-rejected text-status-rejected-fg" },
  stuck: { label: "Stuck", cls: "bg-status-rejected text-status-rejected-fg" },
  running: { label: "Running", cls: "bg-status-prospecting text-status-prospecting-fg" },
  // Not in the design's vocabulary (success / error / stuck) — rendered with
  // the pending tokens rather than silently reusing one of the other three.
  partial: { label: "Partial", cls: "bg-status-pending text-status-pending-fg" },
};

function formatDuration(seconds: number): string {
  const whole = Math.round(seconds);
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  if (m >= 60) return `${Math.floor(m / 60)}h ${m % 60}m`;
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

/**
 * Run reliability and the recent execution log.
 *
 * This panel has its own query, unwindowed on purpose: the design badges it
 * "independent of date filter", and the overview endpoint would window
 * `run_health` along with everything else. Whether the machinery works is not
 * a fact about the reporting period.
 */
export function RunHealthPanel() {
  const query = useRunHealth();

  // Pinned once at mount — an inline Date.now() during render would be an
  // impure read and would move the stuck threshold between renders.
  const [now] = useState(() => Date.now());

  const health = query.data;
  const runs = health?.recent_runs ?? [];
  const succeeded = runs.filter((run) => run.status === "succeeded").length;
  const unscoreablePct =
    health && health.total_cells_fetched > 0
      ? (health.total_cells_unscoreable / health.total_cells_fetched) * 100
      : null;

  const isStuck = (run: (typeof runs)[number]) =>
    run.status === "running" && now - Date.parse(run.started_at) > STUCK_AFTER_MS;

  return (
    <ChartCard
      title="Automation health"
      caption="Run reliability, data completeness, and the recent execution log."
      isPending={query.isPending}
      isError={query.isError}
      error={query.error}
      onRetry={() => query.refetch()}
      isRetrying={query.isFetching}
      isEmpty={runs.length === 0}
      emptyMessage="No research runs have executed yet."
      height={300}
      action={
        <span className="border-border text-muted-foreground shrink-0 rounded border px-1.5 py-0.5 text-[10px] leading-none">
          All time · independent of date filter
        </span>
      }
    >
      <div>
        <div className="mb-5 flex flex-wrap items-start gap-8">
          <Stat
            size="lg"
            value={`${(health?.success_rate ?? 0).toFixed(0)}%`}
            sub={`(${succeeded} of ${runs.length} runs)`}
            label="Run success rate"
          />
          <Stat
            size="lg"
            value={String(health?.total_cells_fetched ?? 0)}
            sub="cells fetched"
            label="Signal data pulled"
          />
          <Stat
            size="lg"
            value={String(health?.total_cells_unscoreable ?? 0)}
            sub={unscoreablePct !== null ? `(${unscoreablePct.toFixed(1)}% of cells)` : undefined}
            label="Unscoreable cells"
          />
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {(
                  [
                    { label: "Time", align: "left" },
                    { label: "Trigger", align: "left" },
                    { label: "Status", align: "left" },
                    { label: "Duration", align: "right" },
                    { label: "Accounts", align: "right" },
                    { label: "Unscoreable", align: "right" },
                    { label: "Created", align: "right" },
                  ] as const
                ).map(({ label, align }) => (
                  <th
                    key={label}
                    scope="col"
                    className={cn(
                      TABLE_HEAD,
                      "first:pl-0 last:pr-0",
                      align === "right" ? "text-right" : "text-left",
                    )}
                  >
                    {label === "Created" ? (
                      <span title="Deduplication skips accounts already in pipeline — 0 is normal">
                        {label}
                      </span>
                    ) : (
                      label
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const stuck = isStuck(run);
                const status: DisplayStatus = stuck ? "stuck" : run.status;
                const badge = STATUS_BADGES[status];
                // A stuck run has no duration yet — show how long it has been
                // running, live, which is the number an operator acts on.
                const duration =
                  run.duration_seconds != null
                    ? formatDuration(run.duration_seconds)
                    : formatDuration((now - Date.parse(run.started_at)) / 1000);

                return (
                  <tr key={run.id} className={cn(TABLE_ROW, stuck && "bg-status-rejected/40")}>
                    <td className={`${TABLE_CELL} px-2 pl-0`}>
                      <span
                        className={cn(
                          "font-mono text-[11px] tabular-nums",
                          stuck ? "text-status-rejected-fg font-medium" : "text-foreground",
                        )}
                        title={new Date(run.started_at).toLocaleString()}
                      >
                        {formatAge(run.started_at)} ago
                      </span>
                      {stuck && (
                        <p className="text-status-rejected-fg mt-0.5 text-[10px] leading-none">
                          May be stuck
                        </p>
                      )}
                    </td>
                    <td className={`${TABLE_CELL} text-muted-foreground px-2 text-[11px] capitalize`}>
                      {run.trigger}
                    </td>
                    <td className={`${TABLE_CELL} px-2`}>
                      <span
                        className={cn(
                          "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] leading-none font-medium",
                          badge.cls,
                        )}
                      >
                        {badge.label}
                      </span>
                    </td>
                    <td className={`${TABLE_CELL} px-2 text-right`}>
                      <span
                        className={cn(
                          "font-mono text-[11px] tabular-nums",
                          stuck ? "text-status-rejected-fg font-medium" : "text-muted-foreground",
                        )}
                      >
                        {duration}
                      </span>
                    </td>
                    <td className={`${TABLE_CELL} px-2 text-right font-mono text-[11px] tabular-nums`}>
                      {run.accounts_scanned === 0 ? (
                        <span className="text-muted-foreground/50">—</span>
                      ) : (
                        run.accounts_scanned
                      )}
                    </td>
                    <td className={`${TABLE_CELL} text-muted-foreground px-2 text-right font-mono text-[11px] tabular-nums`}>
                      {run.cells_unscoreable}
                    </td>
                    <td className={`${TABLE_CELL} px-2 pr-0 text-right font-mono text-[11px] tabular-nums`}>
                      {run.opportunities_created === 0 ? (
                        <span className="text-muted-foreground/40">—</span>
                      ) : (
                        <span className="font-medium">{run.opportunities_created}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <p className="text-muted-foreground mt-3 text-[10px]">
          &ldquo;Created&rdquo; shows &mdash; for most runs because deduplication correctly skips
          accounts already in pipeline. A run marked stuck has reported
          &ldquo;running&rdquo; for over an hour without finishing &mdash; investigate.
        </p>
      </div>
    </ChartCard>
  );
}
