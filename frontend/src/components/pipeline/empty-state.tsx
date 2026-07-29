import { Inbox } from "lucide-react";

/**
 * Names what belongs here and offers the way out, rather than "No data".
 * Which action is offered depends on *why* it's empty: a filtered-to-nothing
 * queue is a filter problem, an unfiltered one means there is genuinely
 * nothing to review.
 */
export function EmptyState({
  filtered,
  onClearFilters,
}: {
  filtered: boolean;
  onClearFilters: () => void;
}) {
  return (
    <div className="bg-card flex flex-col items-center justify-center px-6 py-20">
      <div className="border-border bg-background mb-5 flex size-10 items-center justify-center rounded-lg border">
        <Inbox size={18} strokeWidth={1.5} className="text-muted-foreground" aria-hidden />
      </div>

      <p className="mb-1 text-[13px] font-medium">
        {filtered ? "No opportunities match these filters" : "No opportunities pending review"}
      </p>

      <p className="text-muted-foreground mb-5 max-w-[340px] text-center text-[12px] leading-relaxed">
        {filtered
          ? "Nothing in the pipeline matches the current filters. Widen them to see the rest of the queue."
          : "Scored opportunities appear here as research runs surface new signals on your accounts."}
      </p>

      {filtered && (
        <button
          type="button"
          onClick={onClearFilters}
          className="border-border bg-card hover:bg-accent focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}
