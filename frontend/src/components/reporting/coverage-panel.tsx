"use client";

import { CheckCircle } from "lucide-react";

import { TABLE_CELL, TABLE_HEAD, TABLE_ROW } from "@/components/pipeline/table-chrome";

import { ChartCard } from "./chart-card";
import { Stat } from "./stat";
import type { Overview } from "./use-reporting";

/**
 * Whether every account in territory received signal evaluation. Branches on
 * the data: full coverage gets the success treatment, gaps get the table.
 *
 * The design's gap table mocked Segment / Industry / Last-signal columns; the
 * API returns `{id, name}` per uncovered account and nothing else, so those
 * columns are cut rather than invented.
 */
export function CoveragePanel({
  coverage,
  onRetry,
}: {
  coverage: Overview["account_coverage"];
  onRetry: () => void;
}) {
  const uncovered = coverage.uncovered_accounts;
  const covered = coverage.accounts_with_opportunities;

  return (
    <ChartCard
      title="Account coverage"
      caption="Whether every account in territory received signal evaluation."
      isPending={false}
      isError={false}
      onRetry={onRetry}
      isEmpty={coverage.total_accounts === 0}
      emptyMessage="No accounts are in scope for this range."
      height={240}
    >
      {uncovered.length === 0 ? (
        <div>
          <div className="mb-3 flex items-center gap-2">
            <CheckCircle
              size={18}
              strokeWidth={1.5}
              className="text-status-accepted-fg shrink-0"
              aria-hidden
            />
            <span className="text-[13px] font-semibold">
              All {covered} of {coverage.total_accounts} accounts covered
            </span>
          </div>
          <p className="text-muted-foreground text-[12px] leading-relaxed">
            Every account in your territory has at least one signal evaluated in this period.
            No coverage gaps.
          </p>
          <div className="border-border mt-4 grid grid-cols-3 gap-3 border-t pt-4">
            <Stat value={String(coverage.total_accounts)} label="Accounts in territory" />
            <Stat value={String(covered)} label="With ≥1 signal evaluated" />
            <Stat value="0" label="Uncovered" />
          </div>
        </div>
      ) : (
        <div>
          <div className="mb-3 flex items-baseline gap-2">
            <span className="font-mono text-[13px] font-semibold tabular-nums">
              {covered} of {coverage.total_accounts} covered
            </span>
            <span className="text-muted-foreground text-[11px]">
              — {uncovered.length} uncovered account{uncovered.length === 1 ? "" : "s"}
            </span>
          </div>
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th scope="col" className={`${TABLE_HEAD} pl-0 text-left`}>
                  Account
                </th>
              </tr>
            </thead>
            <tbody>
              {uncovered.map((account) => (
                <tr key={account.id} className={TABLE_ROW}>
                  <td className={`${TABLE_CELL} pl-0 font-medium`}>{account.name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ChartCard>
  );
}
