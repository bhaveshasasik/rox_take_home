"use client";

import { Bar, BarChart, CartesianGrid, LabelList, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";

import { ChartCard } from "./chart-card";
import { useSignalPerformance, type DateWindow } from "./use-reporting";

const CONFIG = { acceptance_rate: { label: "Acceptance rate", color: "var(--chart-1)" } } as const;

export function SignalChart({ window }: { window: DateWindow }) {
  const query = useSignalPerformance(window);

  // Only signals with a decision behind them — an untouched signal has no
  // acceptance rate, and rendering it at 0% would read as "always rejected".
  const data = (query.data ?? [])
    .map((row) => ({
      label: row.label,
      acceptance_rate: row.acceptance_rate,
      decided: row.accepted + row.rejected,
      created: row.created,
    }))
    .filter((row) => row.decided > 0)
    .sort((a, b) => b.acceptance_rate - a.acceptance_rate);

  return (
    <ChartCard
      title="Acceptance by signal type"
      caption="Which signals produce opportunities people actually want — and which generate noise."
      isPending={query.isPending}
      isError={query.isError}
      error={query.error}
      onRetry={() => query.refetch()}
      isRetrying={query.isFetching}
      isEmpty={data.length === 0}
      emptyMessage="No signal has a decision behind it in this range."
      height={260}
    >
      <ChartContainer config={CONFIG} className="h-[228px] w-full">
        <BarChart accessibilityLayer data={data} layout="vertical" margin={{ left: 8, right: 48 }}>
          <CartesianGrid horizontal={false} className="stroke-border" />
          <YAxis
            dataKey="label"
            type="category"
            width={130}
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 11 }}
            className="fill-muted-foreground"
          />
          <XAxis type="number" domain={[0, 100]} hide />
          <ChartTooltip
            content={
              <ChartTooltipContent
                formatter={(value, _name, item) => (
                  <span className="flex w-full justify-between gap-4">
                    <span>{value}% accepted</span>
                    <span className="text-muted-foreground tabular-nums">
                      {item?.payload?.decided ?? 0} of {item?.payload?.created ?? 0} decided
                    </span>
                  </span>
                )}
              />
            }
          />
          <Bar isAnimationActive={false} dataKey="acceptance_rate" fill="var(--color-acceptance_rate)" radius={4} barSize={18}>
            <LabelList
              dataKey="acceptance_rate"
              position="right"
              offset={8}
              className="fill-foreground"
              fontSize={11}
              formatter={(value) => `${value ?? 0}%`}
            />
          </Bar>
        </BarChart>
      </ChartContainer>
    </ChartCard>
  );
}
