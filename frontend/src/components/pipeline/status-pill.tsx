import type { PipelineStatus } from "@/lib/pipeline";
import { cn } from "@/lib/utils";

/**
 * The one place colour is allowed to mean something in the table. Every entry
 * points at a theme token so the pills follow light/dark without edits.
 */
const STATUS_STYLES: Record<PipelineStatus, string> = {
  Pending: "bg-status-pending text-status-pending-fg",
  Accepted: "bg-status-accepted text-status-accepted-fg",
  Rejected: "bg-status-rejected text-status-rejected-fg",
  Prospecting: "bg-status-prospecting text-status-prospecting-fg",
};

export function StatusPill({
  status,
  className,
}: {
  status: PipelineStatus;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-1.5 py-0.5",
        "text-[11px] leading-none font-medium tracking-wide",
        STATUS_STYLES[status],
        className,
      )}
    >
      {status}
    </span>
  );
}
