"use client";

import { formatAge, humanizeStage } from "@/lib/pipeline";
import { cn } from "@/lib/utils";

import type { OpportunityDetail, ResearchSignal } from "./use-opportunity";

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-muted-foreground/70 mb-3 text-[10px] font-semibold tracking-widest uppercase">
      {children}
    </p>
  );
}

function absoluteTime(iso: string) {
  return new Date(iso).toLocaleString();
}

/** Neutral fill — a score bar is a magnitude, not a status, so it carries no colour. */
function ScoreBar({ value }: { value: number }) {
  return (
    <div className="bg-muted h-1 w-full overflow-hidden rounded-full">
      <div className="bg-foreground/25 h-full rounded-full" style={{ width: `${value}%` }} />
    </div>
  );
}

/**
 * The score and the signals that produced it.
 *
 * The design mocks four weighted factors (Signal strength 35%, Account fit 30%,
 * …). No such weights exist — the score is the highest-scoring signal in the
 * research cell, so the real breakdown is the signals themselves. The top row
 * therefore equals the headline number rather than averaging to it.
 */
export function ScoreBreakdown({
  score,
  signals,
}: {
  score: number;
  signals: ResearchSignal[];
}) {
  const scored = signals.filter((s) => s.score !== null && s.score !== undefined);

  return (
    <section className="border-border border-b px-6 py-5">
      <div className="mb-4 flex items-baseline justify-between">
        <SectionLabel>Score breakdown</SectionLabel>
        <span className="font-mono text-[22px] leading-none font-semibold tabular-nums">
          {score}
        </span>
      </div>

      {scored.length === 0 ? (
        <p className="text-muted-foreground text-[12px] leading-relaxed">
          Rox returned narrative research for this account rather than scored
          signals, so there is no per-signal breakdown behind this number.
        </p>
      ) : (
        <div className="space-y-3.5">
          {scored.map((signal, index) => (
            <div key={`${signal.signal}-${index}`}>
              <div className="mb-1.5 flex items-baseline justify-between gap-3">
                <span className="truncate text-[12px]">{signal.signal ?? "Unnamed signal"}</span>
                <span className="font-mono text-[12px] font-medium tabular-nums">
                  {signal.score}
                </span>
              </div>
              <ScoreBar value={signal.score!} />
            </div>
          ))}
          <p className="text-muted-foreground pt-1 text-[10px]">
            The opportunity score is the strongest signal, not an average.
          </p>
        </div>
      )}
    </section>
  );
}

/**
 * Research in labelled sections. Each artifact carries the source (the Rox
 * research column) and when it was fetched; each signal inside it is a section.
 * The design links the source out — there is no URL field, so the source is
 * shown as text rather than a dead link.
 */
export function ResearchSummary({ research }: { research: OpportunityDetail["research"] }) {
  // An artifact contributes either structured signals or a prose narrative.
  // Never `cell_value` — Rox truncates it mid-array, so it is often invalid.
  const artifacts = (research ?? []).filter(
    (a) => (a.signals ?? []).length > 0 || a.narrative,
  );

  return (
    <section className="px-6 py-5">
      <SectionLabel>Research summary</SectionLabel>

      {artifacts.length === 0 ? (
        <p className="text-muted-foreground text-[12px]">
          No research is attached to this opportunity yet.
        </p>
      ) : (
        <div className="space-y-5">
          {artifacts.map((artifact) => {
            const signals = artifact.signals ?? [];
            const source = (
              <div className="text-muted-foreground flex shrink-0 items-center gap-1.5 text-[10px]">
                <span className="truncate">{artifact.column_name ?? "Unknown source"}</span>
                <span className="text-border">·</span>
                <span className="font-mono" title={absoluteTime(artifact.fetched_at)}>
                  {formatAge(artifact.fetched_at)} ago
                </span>
              </div>
            );

            return (
              <div
                key={artifact.id}
                className="border-border space-y-5 border-b pb-5 last:border-0 last:pb-0"
              >
                {signals.length > 0 ? (
                  signals.map((signal, index) => (
                    <article key={index}>
                      <div className="mb-1.5 flex items-center justify-between gap-3">
                        <span className="truncate text-[12px] font-medium">
                          {signal.signal ?? "Unnamed signal"}
                        </span>
                        {index === 0 && source}
                      </div>
                      <p className="text-[12px] leading-[1.65]">
                        {signal.evidence ?? (
                          <span className="text-muted-foreground">
                            No evidence text was returned.
                          </span>
                        )}
                      </p>
                    </article>
                  ))
                ) : (
                  <article>
                    <div className="mb-1.5 flex items-center justify-between gap-3">
                      <span className="truncate text-[12px] font-medium">Narrative research</span>
                      {source}
                    </div>
                    <p className="text-[12px] leading-[1.65]">{artifact.narrative}</p>
                  </article>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

/**
 * Account context, built only from fields that exist. The design's panel lists
 * employees, revenue, segment, industry, HQ and founding year — none of which
 * the API supplies — so this shows the pipeline facts we do have.
 */
export function AccountContext({ detail }: { detail: OpportunityDetail }) {
  const rows: { label: string; value: React.ReactNode }[] = [
    { label: "Account", value: detail.account_name ?? "Unknown" },
    { label: "Signal", value: detail.signal_label ?? detail.signal_type },
    { label: "Stage", value: humanizeStage(detail.stage) },
    { label: "Score", value: <span className="font-mono tabular-nums">{detail.qualification_score}</span> },
    { label: "Needs review", value: detail.needs_review ? "Yes" : "No" },
    { label: "Prospecting", value: detail.has_sequence ? "Sequence created" : "Not started" },
  ];

  return (
    <section className="border-border border-b px-5 py-5">
      <SectionLabel>Account</SectionLabel>
      <dl className="space-y-2">
        {rows.map(({ label, value }) => (
          <div key={label} className="flex items-baseline justify-between gap-3">
            <dt className="text-muted-foreground shrink-0 text-[11px]">{label}</dt>
            <dd className="truncate text-right text-[12px]">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/**
 * Real activity, assembled from the timestamps the record actually carries.
 * The design's "Recent signals" timeline (date + label + intensity) has no
 * backing field; this shows what verifiably happened to this opportunity.
 */
export function ActivityTimeline({ detail }: { detail: OpportunityDetail }) {
  const events: { at: string; label: string; strong?: boolean }[] = [
    { at: detail.created_at, label: "Opportunity created", strong: true },
    ...(detail.research ?? []).map((artifact) => ({
      at: artifact.fetched_at,
      label: `Research fetched · ${artifact.column_name ?? "unknown column"}`,
    })),
    ...(detail.notified_at ? [{ at: detail.notified_at, label: "Reviewer notified" }] : []),
    ...(detail.decision
      ? [
          {
            at: detail.decision.decided_at,
            label: `${detail.decision.decision === "accept" ? "Accepted" : "Rejected"}${
              detail.decision.decided_by ? ` by ${detail.decision.decided_by}` : ""
            }`,
            strong: true,
          },
        ]
      : []),
  ].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());

  return (
    <section className="px-5 py-5">
      <SectionLabel>Activity</SectionLabel>
      <ol className="before:bg-border relative space-y-4 before:absolute before:top-1.5 before:bottom-0 before:left-[3px] before:w-px">
        {events.map((event, index) => (
          <li key={`${event.at}-${index}`} className="relative pl-4">
            <span
              className={cn(
                "absolute top-[5px] left-0 size-[7px] rounded-full border",
                event.strong
                  ? "border-foreground bg-foreground"
                  : "border-muted-foreground bg-muted-foreground",
              )}
              aria-hidden
            />
            <p
              className="text-muted-foreground mb-0.5 font-mono text-[11px] leading-none"
              title={absoluteTime(event.at)}
            >
              {formatAge(event.at)} ago
            </p>
            <p className="text-[12px] leading-snug">{event.label}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
