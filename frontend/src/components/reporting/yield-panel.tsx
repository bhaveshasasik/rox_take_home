"use client";

import { ChartCard } from "./chart-card";
import { Stat } from "./stat";
import type { Overview } from "./use-reporting";

/**
 * What accepted opportunities turn into downstream: accepted → sequences →
 * sent, then the supporting stats. `sent` comes from the funnel's
 * `outreach_sent` step — the yield section has no field for it — and zero
 * there is the system working as designed (emails draft, reps send), so it
 * gets the annotation, never a failure state.
 */
export function YieldPanel({
  prospecting,
  sent,
  onRetry,
}: {
  prospecting: Overview["prospecting_yield"];
  /** `funnel.steps[stage === "outreach_sent"].count` — cross-referenced. */
  sent: number;
  onRetry: () => void;
}) {
  return (
    <ChartCard
      title="Prospecting yield"
      caption="What accepted opportunities turn into downstream."
      isPending={false}
      isError={false}
      onRetry={onRetry}
      isEmpty={prospecting.accepted_opportunities === 0}
      emptyMessage="Nothing has been accepted in this range, so there is nothing to prospect."
      height={240}
    >
      <div>
        {/* accepted → activation% → sequences → sent */}
        <div className="mb-5 flex items-center gap-2">
          <div className="text-center">
            <span className="font-mono text-[28px] leading-none font-semibold tabular-nums">
              {prospecting.accepted_opportunities}
            </span>
            <p className="text-muted-foreground mt-1 text-[10px]">accepted</p>
          </div>
          <div className="flex flex-1 flex-col items-center gap-0.5">
            <div className="bg-border h-px w-full" />
            <span className="text-muted-foreground font-mono text-[10px] tabular-nums">
              {prospecting.activation_rate.toFixed(1)}%
            </span>
          </div>
          <div className="text-center">
            <span className="font-mono text-[28px] leading-none font-semibold tabular-nums">
              {prospecting.sequences_active}
            </span>
            <p className="text-muted-foreground mt-1 text-[10px]">sequences active</p>
          </div>
          <div className="flex flex-1 flex-col items-center gap-0.5">
            <div className="bg-border h-px w-full" />
            <span className="text-muted-foreground text-[10px]">→</span>
          </div>
          <div className="text-center">
            <span className="font-mono text-[28px] leading-none font-semibold tabular-nums">
              {sent}
            </span>
            <p className="text-muted-foreground mt-1 text-[10px]">sent</p>
          </div>
        </div>

        <div className="border-border grid grid-cols-3 gap-3 border-t pt-4">
          <Stat value={String(prospecting.total_contacts)} label="Contacts enrolled" />
          <Stat value={String(prospecting.total_emails_drafted)} label="Emails drafted" />
          <Stat
            value={prospecting.avg_contacts_per_accepted.toFixed(1)}
            label="Contacts per accepted opp"
          />
        </div>

        {sent === 0 && prospecting.total_emails_drafted > 0 && (
          <p className="text-muted-foreground mt-3 text-[11px]">
            Outreach not sent — emails draft and wait for rep approval. Zero is by design.
          </p>
        )}
      </div>
    </ChartCard>
  );
}
