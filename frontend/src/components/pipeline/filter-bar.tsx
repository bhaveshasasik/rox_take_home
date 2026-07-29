"use client";

import { ChevronDown, X } from "lucide-react";

import { cn } from "@/lib/utils";

import type { OpportunityFilters } from "./use-opportunities";

/** The score threshold the design's "Score ≥ 70" chip encodes. */
export const SCORE_THRESHOLD = 70;

function Chip({
  label,
  active,
  onToggle,
  onRemove,
  hasDropdown,
}: {
  label: string;
  active: boolean;
  onToggle: () => void;
  onRemove?: () => void;
  hasDropdown?: boolean;
}) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs leading-none",
        active ? "bg-foreground text-background" : "bg-card border-border text-foreground border",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={active}
        className="focus-visible:ring-ring rounded-sm font-medium focus-visible:ring-1 focus-visible:outline-none"
      >
        {label}
      </button>
      {hasDropdown && (
        <ChevronDown
          size={11}
          strokeWidth={2}
          aria-hidden
          className={active ? "text-background/60" : "text-muted-foreground"}
        />
      )}
      {active && onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${label} filter`}
          className={cn(
            "-mr-0.5 ml-0.5 rounded-sm p-px transition-colors",
            "focus-visible:ring-ring focus-visible:ring-1 focus-visible:outline-none",
            active
              ? "text-background/70 hover:text-background"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <X size={10} strokeWidth={2.5} aria-hidden />
        </button>
      )}
    </div>
  );
}

/**
 * Every control maps to a real `GET /opportunities` query param.
 *
 * The design also shows an "Owner" dropdown and an "Add filter" button. Owner
 * has no backing field, and "Add filter" has no defined behaviour — both are
 * left out rather than rendered as controls that do nothing.
 */
export function FilterBar({
  filters,
  signalOptions,
  onChange,
}: {
  filters: OpportunityFilters;
  signalOptions: { value: string; label: string }[];
  onChange: (next: Partial<OpportunityFilters>) => void;
}) {
  const pendingOnly = filters.status?.length === 1 && filters.status[0] === "new";
  const highScoreOnly = filters.min_score === SCORE_THRESHOLD;
  const activeSignal = filters.signal_type?.[0];

  return (
    <div className="border-border bg-card flex flex-wrap items-center gap-2 border-b px-6 py-2.5">
      <Chip
        label="Pending review"
        active={pendingOnly}
        onToggle={() => onChange({ status: pendingOnly ? undefined : ["new"], offset: 0 })}
        onRemove={() => onChange({ status: undefined, offset: 0 })}
      />

      <Chip
        label={`Score ≥ ${SCORE_THRESHOLD}`}
        active={highScoreOnly}
        onToggle={() =>
          onChange({ min_score: highScoreOnly ? undefined : SCORE_THRESHOLD, offset: 0 })
        }
        onRemove={() => onChange({ min_score: undefined, offset: 0 })}
      />

      {/* Options are derived from the rows currently loaded — the API has no
          endpoint listing signal types, so this reflects the present result
          set rather than every signal that exists. */}
      <div
        className={cn(
          "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs leading-none",
          activeSignal
            ? "bg-foreground text-background"
            : "bg-card border-border text-foreground border",
        )}
      >
        <label htmlFor="signal-filter" className="sr-only">
          Signal type
        </label>
        <select
          id="signal-filter"
          value={activeSignal ?? ""}
          onChange={(event) =>
            onChange({
              signal_type: event.target.value ? [event.target.value] : undefined,
              offset: 0,
            })
          }
          disabled={signalOptions.length === 0}
          className="cursor-pointer appearance-none bg-transparent pr-1 font-medium focus:outline-none disabled:cursor-not-allowed"
        >
          <option value="">Signal type</option>
          {signalOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <ChevronDown
          size={11}
          strokeWidth={2}
          aria-hidden
          className={activeSignal ? "text-background/60" : "text-muted-foreground"}
        />
      </div>
    </div>
  );
}
