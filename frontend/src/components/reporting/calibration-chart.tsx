"use client";

import { Bar, BarChart, CartesianGrid, Cell, LabelList, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";

import { ChartCard } from "./chart-card";
import { LOW_N, type Overview } from "./use-reporting";

const CONFIG = { rate: { label: "Acceptance rate", color: "var(--chart-1)" } } as const;

/**
 * Four fixed score bands, each rate beside its sample size.
 *
 * The two treatments the design encodes are the point of this chart:
 *
 * - `decided === 0` renders as a full-height hatched cell reading "no
 *   decisions" — the API returns `acceptance_rate: 0.0` for that band, and a
 *   real 0% (decisions made, all rejected) must not look like it. The branch
 *   is on `decided`, never on the rate.
 * - `decided < 5` renders faded. Read from the data per band, not hardcoded.
 *
 * The hatch is a stacked sentinel: the no-data band plots 100 units of
 * pattern fill where its zero-length rate bar sits, so no custom shapes are
 * involved and the two bars can never render together.
 */
export function CalibrationChart({
  calibration,
  onRetry,
}: {
  calibration: Overview["score_calibration"];
  onRetry: () => void;
}) {
  const bands = calibration.bands;

  const data = bands.map((band) => {
    const noData = band.decided === 0;
    const lowN = !noData && band.decided < LOW_N;
    return {
      band: band.band,
      rate: noData ? 0 : band.acceptance_rate,
      nodata: noData ? 100 : 0,
      decided: band.decided,
      lowN,
      topLabel: noData ? "" : `${band.acceptance_rate.toFixed(0)}% · n=${band.decided}`,
      hatchLabel: noData ? "no decisions" : "",
    };
  });

  const allLowN = bands.length > 0 && bands.every((band) => band.decided < LOW_N);

  return (
    <ChartCard
      title="Score calibration"
      caption={`Whether the qualification score predicts acceptance — ${calibration.total_decided} decision${calibration.total_decided === 1 ? "" : "s"} total.`}
      isPending={false}
      isError={false}
      onRetry={onRetry}
      isEmpty={calibration.total_decided === 0}
      emptyMessage="Nothing has been decided in this range, so there is no rate to calibrate."
      height={320}
    >
      <ChartContainer config={CONFIG} className="h-[248px] w-full">
        <BarChart accessibilityLayer data={data} margin={{ top: 16, left: -16 }}>
          <defs>
            {/* no-data hatch — visually distinct from a zero-height bar */}
            <pattern
              id="calibration-hatch"
              patternUnits="userSpaceOnUse"
              width={5}
              height={5}
              patternTransform="rotate(-45)"
            >
              <line x1={0} y1={0} x2={0} y2={5} stroke="var(--border)" strokeWidth={1.5} />
            </pattern>
          </defs>
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
                formatter={(_value, name, item) => {
                  const { decided, rate } = item?.payload ?? {};
                  if (name === "nodata") {
                    return <span>No decisions in this band</span>;
                  }
                  return (
                    <span className="flex w-full justify-between gap-4">
                      <span>{rate}% accepted</span>
                      <span className="text-muted-foreground tabular-nums">
                        {decided} decided
                      </span>
                    </span>
                  );
                }}
              />
            }
          />
          <Bar isAnimationActive={false} dataKey="rate" stackId="band" radius={4}>
            {data.map((entry) => (
              <Cell
                key={entry.band}
                fill="var(--color-rate)"
                fillOpacity={entry.lowN ? 0.4 : 0.9}
              />
            ))}
            <LabelList
              dataKey="topLabel"
              position="top"
              offset={6}
              className="fill-muted-foreground"
              fontSize={9}
            />
          </Bar>
          <Bar
            isAnimationActive={false}
            dataKey="nodata"
            stackId="band"
            fill="url(#calibration-hatch)"
            radius={4}
          >
            <LabelList
              dataKey="hatchLabel"
              position="center"
              className="fill-muted-foreground"
              fontSize={9}
            />
          </Bar>
        </BarChart>
      </ChartContainer>

      {/* legend for the two treatments, plus the sample-size caveat */}
      <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px]">
        <span className="flex items-center gap-1.5">
          <span
            className="border-border inline-block h-3 w-3 rounded-sm border"
            style={{
              background:
                "repeating-linear-gradient(-45deg, transparent, transparent 3px, var(--border) 3px, var(--border) 4px)",
            }}
          />
          No decisions in band
        </span>
        <span className="flex items-center gap-1.5">
          <span className="bg-chart-1/40 inline-block h-3 w-3 rounded-sm" />
          n &lt; {LOW_N} — de-emphasized
        </span>
        {allLowN && (
          <span className="text-muted-foreground/70">
            All bands below the confidence threshold at this sample size — read shapes, not
            values.
          </span>
        )}
      </div>
    </ChartCard>
  );
}
