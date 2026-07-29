"use client";

import { REASON_LABELS } from "@/components/opportunity/use-opportunity";

import { ChartCard } from "./chart-card";
import type { Overview } from "./use-reporting";

/**
 * Counts only, per the design: at this volume a time series would be
 * uninformative, so the API's `series`/`group_by` go deliberately unused.
 * Each bar carries its count and the total is the denominator on the panel.
 */
export function RejectionChart({
  rejections,
  onRetry,
}: {
  rejections: Overview["rejection_reasons"];
  onRetry: () => void;
}) {
  const total = rejections.total_rejections;
  const reasons = rejections.reasons;

  return (
    <ChartCard
      title="Rejection reasons"
      caption="What reviewers cite when they reject — counts, not trends, at this volume."
      isPending={false}
      isError={false}
      onRetry={onRetry}
      isEmpty={total === 0}
      emptyMessage="Nothing was rejected in this range."
      height={240}
    >
      <div>
        <div className="mb-4 flex items-baseline gap-1.5">
          <span className="font-mono text-[28px] leading-none font-semibold tabular-nums">
            {total}
          </span>
          <span className="text-muted-foreground text-[11px]">
            rejection{total === 1 ? "" : "s"} total
          </span>
        </div>

        <div className="space-y-2.5">
          {reasons.map((reason) => {
            const code = reason.reason_code;
            const label = code ? (REASON_LABELS[code] ?? code) : "No reason given";
            return (
              <div key={code ?? "unspecified"} className="flex items-center gap-3">
                <span className="text-foreground w-28 shrink-0 text-[12px]">{label}</span>
                <div className="bg-muted h-4 flex-1 overflow-hidden rounded-sm">
                  <div
                    className="bg-foreground/30 h-full rounded-sm"
                    style={{ width: `${(reason.count / total) * 100}%` }}
                  />
                </div>
                <span className="text-foreground w-10 shrink-0 text-right font-mono text-[12px] font-medium tabular-nums">
                  {reason.count} <span className="text-muted-foreground font-sans text-[10px]">of {total}</span>
                </span>
              </div>
            );
          })}
        </div>

        {total > 0 && total < 5 && (
          <p className="text-muted-foreground mt-4 text-[11px]">
            n={total} — too few rejections for a trend; a time-series would be uninformative.
          </p>
        )}
      </div>
    </ChartCard>
  );
}
