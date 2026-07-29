import type { PipelineStatus } from "@/lib/pipeline";
import { cn } from "@/lib/utils";

/**
 * The colour vocabulary for every status pill in the app. Colour is reserved
 * for status and the aging indicator, so this is the only place new tones may
 * be added — and every entry points at a theme token so pills follow
 * light/dark without edits.
 */
export type PillTone = "neutral" | "positive" | "negative" | "info" | "muted";

const TONE_STYLES: Record<PillTone, string> = {
  neutral: "bg-status-pending text-status-pending-fg",
  positive: "bg-status-accepted text-status-accepted-fg",
  negative: "bg-status-rejected text-status-rejected-fg",
  info: "bg-status-prospecting text-status-prospecting-fg",
  // For rows closed without anyone acting — superseded. An absence of a
  // decision, not a decision, so it gets the muted tokens rather than a
  // colour that would read as one.
  muted: "bg-muted text-muted-foreground",
};

/** Base pill. Use this for any status vocabulary other than the pipeline's. */
export function Pill({
  children,
  tone = "neutral",
  className,
}: {
  children: React.ReactNode;
  tone?: PillTone;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-1.5 py-0.5",
        "text-[11px] leading-none font-medium tracking-wide",
        TONE_STYLES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const PIPELINE_TONES: Record<PipelineStatus, PillTone> = {
  Pending: "neutral",
  Accepted: "positive",
  Rejected: "negative",
  Prospecting: "info",
  Superseded: "muted",
};

export function StatusPill({
  status,
  className,
}: {
  status: PipelineStatus;
  className?: string;
}) {
  return (
    <Pill tone={PIPELINE_TONES[status]} className={className}>
      {status}
    </Pill>
  );
}
