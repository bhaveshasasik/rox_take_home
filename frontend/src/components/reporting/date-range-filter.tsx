"use client";

import { cn } from "@/lib/utils";

/**
 * Presets rather than two date inputs: every question these charts answer is
 * "recently, versus before" — and a preset can't produce an inverted range,
 * which the API rejects with a 422.
 */
export const RANGES = [
  { id: "7d", label: "7 days", days: 7 },
  { id: "30d", label: "30 days", days: 30 },
  { id: "90d", label: "90 days", days: 90 },
  { id: "all", label: "All time", days: null },
] as const;

export type RangeId = (typeof RANGES)[number]["id"];

/**
 * `start` only — the window is open-ended at the top so rows created a moment
 * ago are still included. `end` would need a moving "now" and would silently
 * drop the newest data between renders.
 */
export function rangeToWindow(id: RangeId, now: number): { start?: string } {
  const range = RANGES.find((r) => r.id === id);
  if (!range?.days) return {};
  return { start: new Date(now - range.days * 86_400_000).toISOString() };
}

/** Day buckets get unreadable past a few weeks. */
export function rangeToGrouping(id: RangeId): "day" | "week" {
  return id === "7d" || id === "30d" ? "day" : "week";
}

export function DateRangeFilter({
  value,
  onChange,
}: {
  value: RangeId;
  onChange: (next: RangeId) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Date range"
      className="border-border bg-card inline-flex items-center gap-px rounded-md border p-0.5"
    >
      {RANGES.map((range) => (
        <button
          key={range.id}
          type="button"
          onClick={() => onChange(range.id)}
          aria-pressed={value === range.id}
          className={cn(
            "focus-visible:ring-ring rounded-sm px-2.5 py-1 text-[11px] font-medium transition-colors",
            "focus-visible:ring-1 focus-visible:outline-none",
            value === range.id
              ? "bg-foreground text-background"
              : "text-muted-foreground hover:text-foreground hover:bg-accent",
          )}
        >
          {range.label}
        </button>
      ))}
    </div>
  );
}
