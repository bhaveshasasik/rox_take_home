"use client";

import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import type { Schemas } from "@/api/client";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";
import { REASON_LABELS } from "@/components/opportunity/use-opportunity";
import { TABLE_CELL, TABLE_HEAD, TABLE_ROW } from "@/components/pipeline/table-chrome";

import { ChartCard } from "./chart-card";
import { useRejectionReasons, type DateWindow } from "./use-reporting";

/**
 * Fixed slot order — a reason keeps its hue whatever else is on screen, so a
 * shrinking failure mode stays visually the same colour as it shrinks. Never
 * reassigned by rank, never cycled.
 */
const REASON_ORDER: (Schemas["ReasonCode"] | "unspecified")[] = [
  "bad_timing",
  "wrong_persona",
  "already_engaged",
  "low_signal",
  "other",
  "good_fit",
  "unspecified",
];

const SLOT_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
  "var(--muted-foreground)", // unspecified is an absence, not an identity
];

function labelFor(code: string) {
  return code === "unspecified"
    ? "No reason given"
    : (REASON_LABELS[code as Schemas["ReasonCode"]] ?? code);
}

export function RejectionChart({
  window,
  groupBy,
}: {
  window: DateWindow;
  groupBy: "day" | "week";
}) {
  const query = useRejectionReasons(window, groupBy);
  const [showTable, setShowTable] = useState(false);

  // The API returns long-form (period, reason_code, count) so every field stays
  // typed; Recharts stacks wide, so pivot here.
  const { rows, present } = useMemo(() => {
    const series = query.data?.series ?? [];
    const byPeriod = new Map<string, Record<string, number | string>>();
    const seen = new Set<string>();

    for (const point of series) {
      const code = point.reason_code ?? "unspecified";
      seen.add(code);
      const row = byPeriod.get(point.period) ?? { period: point.period };
      row[code] = ((row[code] as number) ?? 0) + point.count;
      byPeriod.set(point.period, row);
    }

    return {
      rows: [...byPeriod.values()].sort((a, b) =>
        String(a.period).localeCompare(String(b.period)),
      ),
      present: REASON_ORDER.filter((code) => seen.has(code)),
    };
  }, [query.data]);

  const config = Object.fromEntries(
    present.map((code) => [
      code,
      { label: labelFor(code), color: SLOT_COLORS[REASON_ORDER.indexOf(code)] },
    ]),
  );

  return (
    <ChartCard
      title="Rejection reasons over time"
      caption="Which failure modes are shrinking and which are not — the reason a reviewer gave, week over week."
      isPending={query.isPending}
      isError={query.isError}
      error={query.error}
      onRetry={() => query.refetch()}
      isRetrying={query.isFetching}
      isEmpty={rows.length === 0}
      emptyMessage="Nothing was rejected in this range."
      height={300}
      action={
        // Relief for the low-contrast light-mode slots, and the table view the
        // accessibility pass requires: identity is never colour-alone.
        <button
          type="button"
          onClick={() => setShowTable((open) => !open)}
          className="border-border bg-card hover:bg-accent focus-visible:ring-ring shrink-0 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none"
          aria-pressed={showTable}
        >
          {showTable ? "Show chart" : "Show data"}
        </button>
      }
    >
      {showTable ? (
        <div className="max-h-[268px] overflow-auto">
          <table className="w-full border-collapse">
            <thead className="bg-background sticky top-0">
              <tr>
                <th scope="col" className={`${TABLE_HEAD} text-left`}>
                  Period
                </th>
                {present.map((code) => (
                  <th key={code} scope="col" className={`${TABLE_HEAD} text-right`}>
                    {labelFor(code)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={String(row.period)} className={TABLE_ROW}>
                  <td className={`${TABLE_CELL} px-3 font-mono`}>{String(row.period)}</td>
                  {present.map((code) => (
                    <td key={code} className={`${TABLE_CELL} px-3 text-right tabular-nums`}>
                      {(row[code] as number) ?? 0}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ChartContainer config={config} className="h-[268px] w-full">
          <BarChart accessibilityLayer data={rows} margin={{ top: 8, left: -20 }}>
            <CartesianGrid vertical={false} className="stroke-border" />
            <XAxis
              dataKey="period"
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 10 }}
              className="fill-muted-foreground"
            />
            <YAxis
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 10 }}
              allowDecimals={false}
              className="fill-muted-foreground"
            />
            <ChartTooltip content={<ChartTooltipContent />} />
            <ChartLegend content={<ChartLegendContent />} />
            {present.map((code, index) => (
              <Bar isAnimationActive={false}
                key={code}
                dataKey={code}
                stackId="reasons"
                fill={SLOT_COLORS[REASON_ORDER.indexOf(code)]}
                // 2px surface gap between stacked segments, and rounded ends
                // only on the topmost segment of each column
                stroke="var(--card)"
                strokeWidth={2}
                radius={index === present.length - 1 ? [4, 4, 0, 0] : 0}
              />
            ))}
          </BarChart>
        </ChartContainer>
      )}
    </ChartCard>
  );
}
