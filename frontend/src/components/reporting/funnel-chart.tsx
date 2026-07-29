"use client";

import { Bar, BarChart, CartesianGrid, LabelList, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { humanizeStage } from "@/lib/pipeline";

import { ChartCard } from "./chart-card";
import { useFunnel, type DateWindow } from "./use-reporting";

/** One series, so one hue — colour here encodes nothing but "this is data". */
const CONFIG = { count: { label: "Opportunities", color: "var(--chart-1)" } } as const;

export function FunnelChart({ window }: { window: DateWindow }) {
  const query = useFunnel(window);

  const data = (query.data?.steps ?? []).map((step) => ({
    stage: humanizeStage(step.stage),
    count: step.count,
    // `pct_of_previous` is null when the step above it is empty — converting
    // out of zero is undefined, and rendering it as 0% would read as a
    // collapse that never happened.
    conversion: step.pct_of_previous,
  }));

  return (
    <ChartCard
      title="Pipeline funnel"
      caption="Where opportunities stop moving, and how much of each stage survives to the next."
      isPending={query.isPending}
      isError={query.isError}
      error={query.error}
      onRetry={() => query.refetch()}
      isRetrying={query.isFetching}
      isEmpty={data.every((d) => d.count === 0)}
      emptyMessage="No opportunities were created in this range."
      height={300}
    >
      <ChartContainer config={CONFIG} className="h-[268px] w-full">
        <BarChart accessibilityLayer data={data} layout="vertical" margin={{ left: 8, right: 56 }}>
          <CartesianGrid horizontal={false} className="stroke-border" />
          <YAxis
            dataKey="stage"
            type="category"
            width={130}
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 11 }}
            className="fill-muted-foreground"
          />
          <XAxis type="number" hide />
          <ChartTooltip
            content={
              <ChartTooltipContent
                formatter={(value, _name, item) => {
                  const conversion = item?.payload?.conversion;
                  return (
                    <span className="flex w-full justify-between gap-4">
                      <span>{value} opportunities</span>
                      <span className="text-muted-foreground tabular-nums">
                        {conversion === null || conversion === undefined
                          ? "—"
                          : `${conversion}% of previous`}
                      </span>
                    </span>
                  );
                }}
              />
            }
          />
          <Bar isAnimationActive={false} dataKey="count" fill="var(--color-count)" radius={4} barSize={18}>
            {/* Direct labels: the conversion rate is the point of the chart, and
                a tooltip would hide it until hover. */}
            <LabelList
              dataKey="count"
              position="right"
              offset={8}
              className="fill-foreground"
              fontSize={11}
              formatter={(value) => String(value ?? "")}
            />
            <LabelList
              dataKey="conversion"
              position="right"
              offset={34}
              className="fill-muted-foreground"
              fontSize={10}
              // null conversion means "undefined", not zero — render nothing
              formatter={(value) => (value === null || value === undefined ? "" : `${value}%`)}
            />
          </Bar>
        </BarChart>
      </ChartContainer>
    </ChartCard>
  );
}
