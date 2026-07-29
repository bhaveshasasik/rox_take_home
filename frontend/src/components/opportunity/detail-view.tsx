"use client";

import { ArrowLeft, Check, FileQuestion } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ApiError } from "@/api/client";
import { ErrorState } from "@/components/pipeline/error-state";
import { StatusPill } from "@/components/pipeline/status-pill";
import { Button } from "@/components/ui/button";
import { formatAge, pipelineStatus } from "@/lib/pipeline";

import { DetailSkeleton } from "./detail-skeleton";
import { RejectDialog } from "./reject-dialog";
import {
  AccountContext,
  ActivityTimeline,
  ResearchSummary,
  ScoreBreakdown,
  SectionLabel,
} from "./sections";
import { REASON_LABELS, useDecide, useOpportunity } from "./use-opportunity";

export function OpportunityDetailView({ id }: { id: string }) {
  const query = useOpportunity(id);
  const decide = useDecide(id);
  const [rejectOpen, setRejectOpen] = useState(false);

  if (query.isPending) return <DetailSkeleton />;

  // A missing id is a different problem from a broken backend, and offering
  // "try again" for a record that does not exist would just fail again.
  if (query.error instanceof ApiError && query.error.status === 404) {
    return <NotFound id={id} />;
  }

  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        onRetry={() => query.refetch()}
        isRetrying={query.isFetching}
      />
    );
  }

  const detail = query.data;
  const accountName = detail.account_name ?? "Unknown account";
  const decided = detail.status !== "new";

  return (
    <>
      <header className="border-border bg-card border-b px-6 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-3">
            <Link
              href="/"
              aria-label="Back to pipeline"
              className="hover:bg-accent hover:text-foreground text-muted-foreground focus-visible:ring-ring -ml-1 mt-px rounded-md p-1 transition-colors focus-visible:ring-1 focus-visible:outline-none"
            >
              <ArrowLeft size={14} strokeWidth={2} aria-hidden />
            </Link>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-[15px] leading-none font-semibold" title={accountName}>
                  {accountName}
                </h1>
                <StatusPill status={pipelineStatus(detail.status, detail.stage)} />
              </div>
              <p className="text-muted-foreground mt-1 truncate text-[12px] leading-none">
                {detail.signal_label ?? detail.signal_type}
                <span className="text-border mx-1.5">·</span>
                surfaced {formatAge(detail.created_at)} ago
              </p>
            </div>
          </div>
        </div>
      </header>

      {/* pb clears the fixed action bar */}
      <div className="flex min-h-0 pb-[57px]">
        <div className="border-border min-w-0 flex-1 border-r">
          <section className="border-border border-b px-6 py-5">
            <SectionLabel>Rationale</SectionLabel>
            <p className="text-[13px] leading-[1.65]">{detail.rationale}</p>
          </section>

          <ScoreBreakdown
            score={detail.qualification_score}
            signals={(detail.research ?? []).flatMap((a) => a.signals ?? [])}
          />

          <ResearchSummary research={detail.research} />
        </div>

        <aside className="w-72 shrink-0">
          <AccountContext detail={detail} />
          <ActivityTimeline detail={detail} />
        </aside>
      </div>

      <div className="border-border bg-card fixed right-0 bottom-0 left-0 z-40 flex items-center justify-between gap-4 border-t px-6 py-3">
        <p className="text-muted-foreground min-w-0 truncate text-[11px]">
          {decided ? <DecisionSummary detail={detail} /> : "Decisions are final — they cannot be undone here."}
        </p>

        <div className="flex shrink-0 items-center gap-2">
          <Button
            type="button"
            variant="outline"
            className="text-[12px]"
            disabled={decided || decide.isPending}
            onClick={() => setRejectOpen(true)}
          >
            Reject
          </Button>
          <Button
            type="button"
            className="text-[12px]"
            disabled={decided || decide.isPending}
            onClick={() => decide.mutate({ decision: "accept" })}
          >
            <Check size={12} strokeWidth={2.5} aria-hidden />
            {decide.isPending && !rejectOpen ? "Accepting…" : "Accept"}
          </Button>
        </div>
      </div>

      {/* An accept failure has no dialog to surface it, so it surfaces here. */}
      {decide.isError && !rejectOpen && (
        <div
          role="alert"
          className="bg-status-rejected text-status-rejected-fg fixed right-6 bottom-[68px] z-40 max-w-sm rounded-md px-3 py-2 text-[11px]"
        >
          {decide.error instanceof ApiError && decide.error.status === 409
            ? "This opportunity has already been decided. Reload to see its current status."
            : "Couldn't record the decision. Try again."}
        </div>
      )}

      <RejectDialog
        open={rejectOpen}
        onOpenChange={(next) => {
          setRejectOpen(next);
          if (!next) decide.reset();
        }}
        accountName={accountName}
        isPending={decide.isPending}
        error={decide.error}
        onConfirm={(input) =>
          decide.mutate(input, {
            // only dismiss on success — on failure the dialog stays open with
            // the reason and note the user already entered
            onSuccess: () => setRejectOpen(false),
          })
        }
      />
    </>
  );
}

function DecisionSummary({ detail }: { detail: ReturnType<typeof useOpportunity>["data"] }) {
  const decision = detail?.decision;
  if (!decision) return <>This opportunity has already been decided.</>;

  return (
    <>
      {decision.decision === "accept" ? "Accepted" : "Rejected"}
      {decision.decided_by ? ` by ${decision.decided_by}` : ""}
      {" · "}
      {formatAge(decision.decided_at)} ago
      {decision.reason_code ? ` · ${REASON_LABELS[decision.reason_code]}` : ""}
      {decision.notes ? ` · ${decision.notes}` : ""}
    </>
  );
}

function NotFound({ id }: { id: string }) {
  return (
    <div className="bg-card flex flex-col items-center justify-center px-6 py-20">
      <div className="border-border bg-background mb-5 flex size-10 items-center justify-center rounded-lg border">
        <FileQuestion size={18} strokeWidth={1.5} className="text-muted-foreground" aria-hidden />
      </div>
      <p className="mb-1 text-[13px] font-medium">Opportunity not found</p>
      <p className="text-muted-foreground mb-5 max-w-[340px] text-center text-[12px] leading-relaxed">
        No opportunity exists with id{" "}
        <span className="font-mono break-all">{id}</span>. It may have been removed, or the
        link may be out of date.
      </p>
      <Link
        href="/"
        className="border-border bg-card hover:bg-accent focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none"
      >
        <ArrowLeft size={11} strokeWidth={2} aria-hidden />
        Back to pipeline
      </Link>
    </div>
  );
}
