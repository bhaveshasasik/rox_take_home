"use client";

import { AlertTriangle, Loader2, RefreshCw, Users } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/pipeline/error-state";
import { Pill } from "@/components/pipeline/status-pill";
import { TABLE_HEAD } from "@/components/pipeline/table-chrome";
import { Skeleton } from "@/components/ui/skeleton";
import { formatAge } from "@/lib/pipeline";

import { ContactsTable } from "./contacts-table";
import { EmailDialog } from "./email-dialog";
import {
  hasStoppedPolling,
  isNotStarted,
  useProspecting,
  useRerunProspecting,
  type Enrollment,
} from "./use-prospecting";

export function ProspectingTab({ opportunityId }: { opportunityId: string }) {
  const query = useProspecting(opportunityId, true);
  const rerun = useRerunProspecting(opportunityId);
  const [openEnrollment, setOpenEnrollment] = useState<Enrollment | null>(null);

  if (query.isPending) return <ContactsSkeleton />;

  // A 404 is not a failure here — prospecting is a background task started on
  // accept, so "nothing yet" is the expected answer for its first seconds.
  if (isNotStarted(query.error)) {
    const gaveUp = hasStoppedPolling(query.errorUpdateCount);
    return (
      <InProgress
        gaveUp={gaveUp}
        isChecking={query.isFetching || rerun.isPending}
        onCheckAgain={() => query.refetch()}
        onRerun={() => rerun.mutate()}
      />
    );
  }

  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        onRetry={() => query.refetch()}
        isRetrying={query.isFetching}
        title="Failed to load prospecting"
      />
    );
  }

  const sequence = query.data;
  const enrollments = sequence.enrollments ?? [];

  if (sequence.status === "failed") {
    return (
      <Failed
        error={sequence.error}
        onRetry={() => rerun.mutate()}
        isRetrying={rerun.isPending}
        rerunError={rerun.error}
      />
    );
  }

  return (
    <>
      <div className="border-border flex items-center justify-between gap-3 border-b px-6 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-[12px] font-medium" title={sequence.name}>
            {sequence.name}
          </span>
          <Pill tone={sequence.status === "completed" ? "positive" : "info"}>
            {sequence.status}
          </Pill>
        </div>
        <span className="text-muted-foreground shrink-0 text-[11px]">
          {enrollments.length} {enrollments.length === 1 ? "contact" : "contacts"} · created{" "}
          {formatAge(sequence.created_at)} ago
        </span>
      </div>

      {enrollments.length === 0 ? (
        <NoContacts onRerun={() => rerun.mutate()} isRetrying={rerun.isPending} />
      ) : (
        <ContactsTable enrollments={enrollments} onViewEmails={setOpenEnrollment} />
      )}

      <EmailDialog enrollment={openEnrollment} onClose={() => setOpenEnrollment(null)} />
    </>
  );
}

/**
 * Distinct from an empty state: the work is running, not absent. The user
 * should wait, not act — until polling gives up, at which point they can.
 */
function InProgress({
  gaveUp,
  isChecking,
  onCheckAgain,
  onRerun,
}: {
  gaveUp: boolean;
  isChecking: boolean;
  onCheckAgain: () => void;
  onRerun: () => void;
}) {
  return (
    <div className="bg-card flex flex-col items-center justify-center px-6 py-20">
      <div className="border-border bg-background mb-5 flex size-10 items-center justify-center rounded-lg border">
        {gaveUp ? (
          <Users size={18} strokeWidth={1.5} className="text-muted-foreground" aria-hidden />
        ) : (
          <Loader2
            size={18}
            strokeWidth={1.5}
            className="text-muted-foreground animate-spin"
            aria-hidden
          />
        )}
      </div>

      <p className="mb-1 text-[13px] font-medium">
        {gaveUp ? "Prospecting hasn’t returned contacts" : "Finding contacts…"}
      </p>
      <p className="text-muted-foreground mb-5 max-w-[360px] text-center text-[12px] leading-relaxed">
        {gaveUp
          ? "It was started when this opportunity was accepted but hasn’t produced a sequence. It may have failed without recording a reason."
          : "Prospecting started when this opportunity was accepted. Contacts and draft emails appear here as soon as it finishes."}
      </p>

      {gaveUp && (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onCheckAgain}
            disabled={isChecking}
            className="border-border bg-card hover:bg-accent focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none disabled:opacity-40"
          >
            <RefreshCw
              size={11}
              strokeWidth={2}
              aria-hidden
              className={isChecking ? "animate-spin" : undefined}
            />
            Check again
          </button>
          <button
            type="button"
            onClick={onRerun}
            disabled={isChecking}
            className="border-border bg-card hover:bg-accent text-muted-foreground hover:text-foreground focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none disabled:opacity-40"
          >
            Run prospecting
          </button>
        </div>
      )}
    </div>
  );
}

/** The backend retries a FAILED sequence in place, so re-running is the fix. */
function Failed({
  error,
  onRetry,
  isRetrying,
  rerunError,
}: {
  error: string | null | undefined;
  onRetry: () => void;
  isRetrying: boolean;
  rerunError: unknown;
}) {
  return (
    <div className="bg-card flex flex-col items-center justify-center px-6 py-20">
      <div className="bg-status-rejected mb-5 flex size-10 items-center justify-center rounded-lg">
        <AlertTriangle
          size={18}
          strokeWidth={1.5}
          className="text-status-rejected-fg"
          aria-hidden
        />
      </div>
      <p className="mb-1 text-[13px] font-medium">Prospecting failed</p>
      <p className="text-muted-foreground mb-5 max-w-[380px] text-center text-[12px] leading-relaxed">
        {/* the backend's own reason, e.g. no contacts found for the account */}
        {error || "No reason was recorded."}
      </p>
      <button
        type="button"
        onClick={onRetry}
        disabled={isRetrying}
        className="border-border bg-card hover:bg-accent focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none disabled:opacity-40"
      >
        <RefreshCw
          size={11}
          strokeWidth={2}
          aria-hidden
          className={isRetrying ? "animate-spin" : undefined}
        />
        {isRetrying ? "Retrying…" : "Retry prospecting"}
      </button>
      {rerunError != null && (
        <p role="alert" className="text-status-rejected-fg mt-3 text-[11px]">
          Retry failed. Try again in a moment.
        </p>
      )}
    </div>
  );
}

function NoContacts({ onRerun, isRetrying }: { onRerun: () => void; isRetrying: boolean }) {
  return (
    <div className="bg-card flex flex-col items-center justify-center px-6 py-20">
      <div className="border-border bg-background mb-5 flex size-10 items-center justify-center rounded-lg border">
        <Users size={18} strokeWidth={1.5} className="text-muted-foreground" aria-hidden />
      </div>
      <p className="mb-1 text-[13px] font-medium">No contacts were enrolled</p>
      <p className="text-muted-foreground mb-5 max-w-[340px] text-center text-[12px] leading-relaxed">
        A sequence exists but nobody was enrolled — Rox returned no people for this account.
      </p>
      <button
        type="button"
        onClick={onRerun}
        disabled={isRetrying}
        className="border-border bg-card hover:bg-accent focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none disabled:opacity-40"
      >
        Run again
      </button>
    </div>
  );
}

/** Matches the loaded table's header and column rhythm so nothing shifts. */
function ContactsSkeleton() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading prospecting…</span>
      <div className="border-border flex items-center justify-between border-b px-6 py-3">
        <Skeleton className="h-[12px] w-48" />
        <Skeleton className="h-[11px] w-32" />
      </div>
      <table className="w-full table-fixed border-collapse">
        <thead className="bg-background">
          <tr>
            {["Contact", "Reach", "Why selected", "Status", "Emails"].map((label, index) => (
              <th
                key={label}
                scope="col"
                className={`${TABLE_HEAD} ${index === 0 ? "w-[24%] pl-6" : ""} ${
                  index === 4 ? "pr-6 text-right" : "text-left"
                }`}
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="bg-card">
          {[0, 1, 2].map((row) => (
            <tr key={row} className="border-border border-b last:border-0">
              <td className="py-2 pr-3 pl-6">
                <Skeleton className="h-[13px] w-28" />
                <Skeleton className="mt-1.5 h-[11px] w-20" />
              </td>
              <td className="px-3 py-2">
                <Skeleton className="h-[12px] w-32" />
                <Skeleton className="mt-1.5 h-[11px] w-16" />
              </td>
              <td className="px-3 py-2">
                <Skeleton className="h-[12px] w-full" />
                <Skeleton className="mt-1.5 h-[12px] w-2/3" />
              </td>
              <td className="px-3 py-2">
                <Skeleton className="h-[17px] w-14 rounded-sm" />
              </td>
              <td className="py-2 pr-6 pl-3">
                <Skeleton className="ml-auto h-[22px] w-16 rounded-md" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
