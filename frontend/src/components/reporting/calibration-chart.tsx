"use client";

import { Bar, BarChart, CartesianGrid, LabelList, ReferenceLine, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";

import { ChartCard } from "./chart-card";
import { useScoreCalibration, type DateWindow } from "./use-reporting";

const CONFIG = { acceptance_rate: { label: "Acceptance rate", color: "var(--chart-1)" } } as const;

/**
 * The question is whether a higher score actually means a higher acceptance
 * rate. A flat line means the score is decoration — so the chart carries the
 * overall rate as a reference line to compare each decile against, and shows
 * the sample size per bucket, since a 100% rate on one decision is noise.
 */
export function CalibrationChart({ window }: { window: DateWindow }) {
  const query = useScoreCalibration(window);

  const bands = query.data?.bands ?? [];
  const data = bands.map((band) => ({
    band: band.band,
    lo: band.lo,
    acceptance_rate: band.acceptance_rate,
    decided: band.decided,
  }));

  const totalDecided = query.data?.total_decided ?? 0;
  const totalAccepted = bands.reduce((sum, band) => sum + band.accepted, 0);
  const overall = totalDecided ? (totalAccepted / totalDecided) * 100 : 0;

  return (
    <ChartCard
      title="Score calibration"
      caption="Whether the qualification score predicts acceptance — a flat shape means it doesn't."
      isPending={query.isPending}
      isError={query.isError}
      error={query.error}
      onRetry={() => query.refetch()}
      isRetrying={query.isFetching}
      isEmpty={totalDecided === 0}
      emptyMessage="Nothing has been decided in this range, so there is no rate to calibrate."
      height={300}
    >
      <ChartContainer config={CONFIG} className="h-[268px] w-full">
        <BarChart accessibilityLayer data={data} margin={{ top: 16, left: -16 }}>
          <CartesianGrid vertical={false} className="stroke-border" />
          <XAxis
            dataKey="band"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 10 }}
            interval={0}
            className="fill-muted-foreground"
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 10 }}
            domain={[0, 100]}
            unit="%"
            className="fill-muted-foreground"
          />
          <ChartTooltip
            content={
              <ChartTooltipContent
                labelFormatter={(label) => `Score ${label}`}
                formatter={(value, _name, item) => (
                  <span className="flex w-full justify-between gap-4">
                    <span>{value}% accepted</span>
                    <span className="text-muted-foreground tabular-nums">
                      {item?.payload?.decided ?? 0} decided
                    </span>
                  </span>
                )}
              />
            }
          />
          {totalDecided > 0 && (
            <ReferenceLine
              y={overall}
              strokeDasharray="4 4"
              className="stroke-muted-foreground"
              label={{
                value: `overall ${overall.toFixed(0)}%`,
                position: "insideTopLeft",
                fontSize: 10,
                className: "fill-muted-foreground",
              }}
            />
          )}
          <Bar isAnimationActive={false} dataKey="acceptance_rate" fill="var(--color-acceptance_rate)" radius={4}>
            {/* sample size, not the rate — a 100% bucket built on one decision
                should not read the same as one built on forty */}
            <LabelList
              dataKey="decided"
              position="top"
              offset={6}
              className="fill-muted-foreground"
              fontSize={9}
              formatter={(value) => (value ? `n=${value}` : "")}
            />
          </Bar>
        </BarChart>
      </ChartContainer>
    </ChartCard>
  );
}
