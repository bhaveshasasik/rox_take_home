"use client";

import { Bar, BarChart, CartesianGrid, LabelList, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { humanizeStage } from "@/lib/pipeline";

import { ChartCard } from "./chart-card";
import type { Overview } from "./use-reporting";

/** One series, so one hue — colour here encodes nothing but "this is data". */
const CONFIG = { count: { label: "Opportunities", color: "var(--chart-1)" } } as const;

/**
 * `outreach_sent` at zero is the system working as intended — emails draft and
 * wait for a rep, they never send themselves. The design gives that stage a
 * dashed outline instead of an empty slot, so "zero by design" cannot be read
 * as a collapse. Rendered as a stacked sentinel segment: for the not-sent
 * stage the real bar is zero-length, so the dashed segment is all that shows;
 * everywhere else the sentinel is zero-length and invisible.
 */
export function FunnelChart({
  funnel,
  drafted,
  onRetry,
}: {
  funnel: Overview["funnel"];
  drafted: number;
  onRetry: () => void;
}) {
  const steps = funnel.steps;
  const maxCount = Math.max(...steps.map((s) => s.count), 1);

  const data = steps.map((step) => {
    const notSentByDesign = step.stage === "outreach_sent" && step.count === 0;
    return {
      stage: humanizeStage(step.stage),
      count: step.count,
      // `pct_of_previous` is null when the step above it is empty — converting
      // out of zero is undefined, and rendering it as 0% would read as a
      // collapse that never happened.
      conversion: step.pct_of_previous,
      // Precomputed: a LabelList formatter sees only its value, and the
      // not-sent stage must label "not sent" where its 0% would otherwise be.
      conversionLabel: notSentByDesign
        ? "not sent"
        : step.pct_of_previous === null || step.pct_of_previous === undefined
          ? ""
          : `${step.pct_of_previous}%`,
      notSentByDesign,
      sentinel: notSentByDesign ? maxCount * 0.06 : 0,
    };
  });

  return (
    <ChartCard
      title="Pipeline funnel"
      caption="How opportunities move from creation to outreach, and where they fall off."
      isPending={false}
      isError={false}
      onRetry={onRetry}
      isEmpty={data.every((d) => d.count === 0)}
      emptyMessage="No opportunities were created in this range."
      height={320}
    >
      <ChartContainer config={CONFIG} className="h-[240px] w-full">
        <BarChart accessibilityLayer data={data} layout="vertical" margin={{ left: 8, right: 96 }}>
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
                  const { conversion, notSentByDesign } = item?.payload ?? {};
                  return (
                    <span className="flex w-full justify-between gap-4">
                      <span>{value} opportunities</span>
                      <span className="text-muted-foreground tabular-nums">
                        {notSentByDesign
                          ? "not sent — drafts pending approval"
                          : conversion === null || conversion === undefined
                            ? "—"
                            : `${conversion}% of previous`}
                      </span>
                    </span>
                  );
                }}
              />
            }
          />
          <Bar
            isAnimationActive={false}
            dataKey="count"
            stackId="funnel"
            fill="var(--color-count)"
            radius={4}
            barSize={18}
          />
          {/* the dashed zero-by-design segment; row labels live here so they
              render past the end of both stack segments */}
          <Bar
            isAnimationActive={false}
            dataKey="sentinel"
            stackId="funnel"
            fill="transparent"
            stroke="var(--border)"
            strokeWidth={1}
            strokeDasharray="4 3"
            radius={4}
            barSize={18}
          >
            <LabelList
              dataKey="count"
              position="right"
              offset={8}
              className="fill-foreground"
              fontSize={11}
              formatter={(value) => String(value ?? "")}
            />
            {/* Direct labels: the conversion rate is the point of the chart,
                and a tooltip would hide it until hover. */}
            <LabelList
              dataKey="conversionLabel"
              position="right"
              offset={30}
              className="fill-muted-foreground"
              fontSize={10}
            />
          </Bar>
        </BarChart>
      </ChartContainer>

      {/* the two annotations the design carries under the chart */}
      <div className="mt-3 flex flex-col gap-1">
        {funnel.rejected > 0 && (
          <p className="text-muted-foreground text-[11px]">
            <span className="text-foreground font-medium">Accepted:</span> {funnel.rejected}{" "}
            rejected at review
          </p>
        )}
        {data.some((d) => d.notSentByDesign) && (
          <p className="text-muted-foreground text-[11px]">
            <span className="text-foreground font-medium">Outreach sent:</span> {drafted} drafted,
            approval pending — zero sent is by design, not a failure
          </p>
        )}
      </div>
    </ChartCard>
  );
}
