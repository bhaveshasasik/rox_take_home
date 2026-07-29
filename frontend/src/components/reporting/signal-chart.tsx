"use client";

import { cn } from "@/lib/utils";

import { ChartCard } from "./chart-card";
import { LOW_N, type Overview } from "./use-reporting";

/**
 * One row per signal type: created, decided and accepted as three layered
 * widths from the same origin, so the gap between layers is pipeline depth —
 * a wide created bar over a narrow decided bar is a backlog, not a verdict.
 *
 * "Decided" is not an API field; it is `accepted + rejected`, which the API's
 * own `acceptance_rate` is defined against (verified against live data).
 *
 * Not a Recharts chart on purpose: three same-origin overlapping widths per
 * category is not a shape BarChart draws — stacking would sum the layers and
 * grouping would place them side by side, both of which misrepresent. The
 * layers are plain divs on theme tokens.
 */
export function SignalChart({
  rows,
  onRetry,
}: {
  rows: Overview["signal_performance"];
  onRetry: () => void;
}) {
  const data = rows
    .map((row) => {
      const decided = row.accepted + row.rejected;
      return {
        key: row.signal_type,
        label: row.label,
        created: row.created,
        decided,
        accepted: row.accepted,
        rate: decided > 0 ? row.acceptance_rate : null,
        lowN: decided > 0 && decided < LOW_N,
      };
    })
    .sort((a, b) => b.created - a.created);

  const maxCreated = Math.max(...data.map((d) => d.created), 1);

  return (
    <ChartCard
      title="Signal performance"
      caption="Acceptance rate per signal type — the gap between created and decided is pipeline depth."
      isPending={false}
      isError={false}
      onRetry={onRetry}
      isEmpty={data.length === 0}
      emptyMessage="No opportunities carry a signal type in this range."
      height={260}
    >
      <div className="space-y-3">
        <div className="text-muted-foreground flex items-center gap-3 text-[10px] tracking-wider uppercase">
          <span className="w-40 shrink-0">Signal type</span>
          <span className="flex-1">Created / decided / accepted</span>
          <span className="w-44 shrink-0 text-right">Rate</span>
        </div>

        {data.map((row) => (
          <div key={row.key} className="flex items-center gap-3">
            <span className="text-foreground w-40 shrink-0 truncate text-[12px]" title={row.label}>
              {row.label}
            </span>

            <div className="relative flex h-5 flex-1 items-center">
              <div
                className="bg-foreground/8 absolute top-0 left-0 h-full rounded-sm"
                style={{ width: `${(row.created / maxCreated) * 100}%` }}
              />
              <div
                className="bg-foreground/16 absolute top-0 left-0 h-full rounded-sm"
                style={{ width: `${(row.decided / maxCreated) * 100}%` }}
              />
              <div
                className={cn(
                  "bg-foreground absolute top-0 left-0 h-full rounded-sm",
                  row.lowN ? "opacity-25" : "opacity-45",
                )}
                style={{ width: `${(row.accepted / maxCreated) * 100}%` }}
              />
            </div>

            <div className="flex w-44 shrink-0 items-baseline justify-end gap-2">
              {row.rate === null ? (
                // No decisions yet — a dash, never 0%. Zero and no-data are
                // different facts and must not share a rendering.
                <span className="text-muted-foreground/50 text-[12px]">—</span>
              ) : (
                <span
                  className={cn(
                    "font-mono text-[12px] font-medium tabular-nums",
                    row.lowN ? "text-muted-foreground" : "text-foreground",
                  )}
                >
                  {row.rate.toFixed(0)}%
                </span>
              )}
              <span className="text-muted-foreground text-[10px] whitespace-nowrap">
                {row.rate === null
                  ? `0 decided · ${row.created} created`
                  : `${row.accepted} of ${row.decided} decided · ${row.created} created`}
              </span>
            </div>
          </div>
        ))}

        <div className="text-muted-foreground flex items-center gap-4 pt-1 text-[10px]">
          <span className="flex items-center gap-1.5">
            <span className="bg-foreground/8 inline-block h-2 w-10 rounded-sm" />
            Created
          </span>
          <span className="flex items-center gap-1.5">
            <span className="bg-foreground/16 inline-block h-2 w-10 rounded-sm" />
            Decided
          </span>
          <span className="flex items-center gap-1.5">
            <span className="bg-foreground/45 inline-block h-2 w-10 rounded-sm" />
            Accepted
          </span>
          <span className="text-muted-foreground/60 ml-1">Faded bars: n &lt; {LOW_N}</span>
        </div>
      </div>
    </ChartCard>
  );
}
