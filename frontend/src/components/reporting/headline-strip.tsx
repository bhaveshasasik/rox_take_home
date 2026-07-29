"use client";

import type { Overview } from "./use-reporting";
import { Stat } from "./stat";

/**
 * Raw counts, then the acceptance rate under *both* denominators.
 *
 * The API's `acceptance_rate` is already the share of decided (8 of 11).
 * The design deliberately shows the share of all opportunities beside it,
 * de-emphasized — 38% including pending is a different, weaker claim than
 * 72.7% of decided, and showing only one invites reading it as the other.
 */
export function HeadlineStrip({ headline }: { headline: Overview["headline"] }) {
  const decided = headline.accepted + headline.rejected;
  const ofAll =
    headline.total_opportunities > 0
      ? (headline.accepted / headline.total_opportunities) * 100
      : null;

  return (
    <div className="border-border bg-card border-b px-6 py-4">
      <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
        <div className="border-border flex items-start gap-6 sm:border-r sm:pr-8">
          <Stat size="lg" value={String(headline.total_opportunities)} label="Opportunities" />
          <Stat size="lg" value={String(headline.pending_review)} label="Pending review" />
          <Stat size="lg" value={String(headline.accepted)} label="Accepted" />
          <Stat size="lg" value={String(headline.rejected)} label="Rejected" />
        </div>

        {decided > 0 ? (
          <div className="flex items-start gap-6">
            <Stat
              size="lg"
              value={`${headline.acceptance_rate.toFixed(1)}%`}
              sub={`(${headline.accepted} of ${decided} decided)`}
              label="acceptance rate of decided"
            />
            {ofAll !== null && (
              <Stat
                size="lg"
                muted
                value={`${ofAll.toFixed(0)}%`}
                sub={`(${headline.accepted} of ${headline.total_opportunities} all)`}
                label="incl. pending"
              />
            )}
          </div>
        ) : (
          <p className="text-muted-foreground self-center text-[12px]">
            No decisions in this range yet — acceptance rates appear once something is decided.
          </p>
        )}
      </div>
    </div>
  );
}
