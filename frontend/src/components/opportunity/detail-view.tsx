"use client";

import { ArrowLeft, Check, FileQuestion } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { ApiError } from "@/api/client";
import { ErrorState } from "@/components/pipeline/error-state";
import { StatusPill } from "@/components/pipeline/status-pill";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { formatAge, pipelineStatus } from "@/lib/pipeline";

import { DetailSkeleton } from "./detail-skeleton";
import { ProspectingTab } from "./prospecting-tab";
import { RejectDialog } from "./reject-dialog";
import {
  AccountContext,
  ActivityTimeline,
  RecentSignals,
  ResearchSummary,
  ScoreBreakdown,
  SectionLabel,
} from "./sections";
import { REASON_LABELS, useDecide, useOpportunity } from "./use-opportunity";

export function OpportunityDetailView({ id }: { id: string }) {
  const query = useOpportunity(id);
  const decide = useDecide(id);
  const [rejectOpen, setRejectOpen] = useState(false);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

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
  // Prospecting only ever runs for accepted opportunities.
  const isAccepted = detail.status === "accepted";

  // The tab lives in the URL so a prospecting view is linkable and the back
  // button leaves it. `?tab=prospecting` on a non-accepted opportunity falls
  // back to Overview rather than showing a disabled tab's content.
  const activeTab =
    searchParams.get("tab") === "prospecting" && isAccepted ? "prospecting" : "overview";

  const selectTab = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    // Overview is the default, so it stays out of the URL.
    if (value === "overview") params.delete("tab");
    else params.set("tab", value);
    const query = params.toString();
    router.push(query ? `${pathname}?${query}` : pathname, { scroll: false });
  };

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
      <Tabs value={activeTab} onValueChange={selectTab} className="min-h-0 gap-0 pb-[57px]">
        <TabsList className="border-border bg-card h-auto w-full justify-start rounded-none border-b p-0">
          <TabTrigger value="overview">Overview</TabTrigger>
          {/* Disabled rather than hidden: the capability exists, it is just not
              available yet — which is also what the API says, returning 409
              "prospecting only runs for accepted opportunities" rather than a
              404. Hiding it would leave a reviewer unsure prospecting exists. */}
          <TabTrigger
            value="prospecting"
            disabled={!isAccepted}
            title={
              isAccepted ? undefined : "Prospecting runs once this opportunity is accepted"
            }
          >
            Prospecting
          </TabTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex min-h-0">
          <div className="border-border min-w-0 flex-1 border-r">
            <section className="border-border border-b px-6 py-5">
              <SectionLabel>Rationale</SectionLabel>
              <Rationale text={detail.rationale} />
            </section>

            <ScoreBreakdown
              score={detail.qualification_score}
              breakdown={detail.score_breakdown}
              signals={(detail.research ?? []).flatMap((a) => a.signals ?? [])}
            />

            <ResearchSummary
              research={detail.research}
              extractedSignals={detail.extracted_signals}
            />
          </div>

          <aside className="w-72 shrink-0">
            <AccountContext detail={detail} />
            <RecentSignals signals={detail.extracted_signals ?? []} />
            <ActivityTimeline detail={detail} />
          </aside>
        </TabsContent>

        <TabsContent value="prospecting" className="min-h-0">
          {/* mounted only when selected, so polling never starts for an
              opportunity whose prospecting tab was never opened */}
          {isAccepted && <ProspectingTab opportunityId={detail.id} />}
        </TabsContent>
      </Tabs>

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

/**
 * The stored rationale is more than one paragraph: `write_brief` joins the
 * brief body to its why-now clause with a blank line, and the legacy parser
 * joins evidence spans the same way. HTML collapses that whitespace, so the
 * break only survives as real elements — rendering the raw string ran the
 * timing sentence straight onto the end of the rationale.
 */
function Rationale({ text }: { text: string }) {
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  if (paragraphs.length === 0) {
    return (
      <p className="text-muted-foreground text-[13px] leading-[1.65] italic">
        No rationale was recorded for this opportunity.
      </p>
    );
  }

  return (
    <div className="space-y-2.5">
      {paragraphs.map((paragraph, index) => (
        <p key={index} className="text-[13px] leading-[1.65]">
          {paragraph}
        </p>
      ))}
    </div>
  );
}

/** Underlined-on-active tab, matching the flat chrome the rest of the app uses. */
function TabTrigger({
  value,
  children,
  disabled,
  title,
}: {
  value: string;
  children: React.ReactNode;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <TabsTrigger
      value={value}
      disabled={disabled}
      title={title}
      className={cn(
        // shadcn's trigger is `flex-1`, which stretches two tabs across the
        // full width; these sit left-aligned at their natural size.
        "flex-none rounded-none border-0 border-b-2 border-transparent bg-transparent px-6 py-2.5",
        "text-muted-foreground text-[12px] font-medium shadow-none",
        "data-[state=active]:border-foreground data-[state=active]:text-foreground",
        "data-[state=active]:bg-transparent data-[state=active]:shadow-none",
        "disabled:pointer-events-auto disabled:cursor-not-allowed disabled:opacity-40",
      )}
    >
      {children}
    </TabsTrigger>
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
