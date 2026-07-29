"use client";

import { ChartCard } from "./chart-card";
import { Stat } from "./stat";
import type { Overview } from "./use-reporting";

/**
 * Median and p90 hours to a decision, and — because the design insists — the
 * measurement-basis callout: some decisions are timed from notification and
 * some from creation, and a blended median without that caveat overstates
 * its own precision.
 */
export function LatencyPanel({
  latency,
  onRetry,
}: {
  latency: Overview["decision_latency"];
  onRetry: () => void;
}) {
  const mixedBasis = latency.from_notification > 0 && latency.from_creation > 0;

  return (
    <ChartCard
      title="Decision latency"
      caption="How quickly reviewers triage opportunities after they appear."
      isPending={false}
      isError={false}
      onRetry={onRetry}
      isEmpty={latency.measured === 0}
      emptyMessage="No decisions carry timestamps in this range, so there is nothing to measure."
      height={240}
    >
      <div>
        <div className="mb-4 flex items-end gap-6">
          <Stat
            size="lg"
            value={latency.median_hours != null ? `${latency.median_hours.toFixed(1)}h` : "—"}
            label="median time to decision"
          />
          <Stat
            value={latency.p90_hours != null ? `${latency.p90_hours.toFixed(1)}h` : "—"}
            label="p90"
          />
          <Stat value={String(latency.measured)} label="decisions measured" />
        </div>

        {mixedBasis && (
          <div className="border-border bg-background rounded border px-3 py-2.5">
            <p className="text-muted-foreground text-[11px] leading-relaxed">
              <span className="text-foreground font-medium">Measurement basis differs:</span>{" "}
              {latency.from_notification} of {latency.count} decisions are timed from
              notification; {latency.from_creation} from creation (notification not recorded).
              The median blends both — interpret with care.
            </p>
          </div>
        )}
      </div>
    </ChartCard>
  );
}
