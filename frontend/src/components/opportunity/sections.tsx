"use client";

import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { formatAge, humanizeStage } from "@/lib/pipeline";
import { cn } from "@/lib/utils";

import type {
  ExtractedSignal,
  OpportunityDetail,
  ResearchSignal,
  SourceRef,
} from "./use-opportunity";

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

/**
 * A magnitude bar. Positive contributions carry no colour — magnitude is not a
 * status. Deductions do, because "this lowered the score" is meaning rather
 * than decoration, and a signed number alone is easy to skim past.
 */
function ScoreBar({ value }: { value: number }) {
  return (
    <div className="bg-muted h-1 w-full overflow-hidden rounded-full">
      <div
        className={cn(
          "h-full rounded-full",
          value < 0 ? "bg-age-overdue/40" : "bg-foreground/25",
        )}
        style={{ width: `${Math.min(100, Math.abs(value))}%` }}
      />
    </div>
  );
}

/** Factor names are a fixed backend vocabulary; anything unmapped degrades to
 *  a humanised form rather than rendering a raw slug. */
const FACTOR_LABELS: Record<string, string> = {
  signal_strength: "Signal strength",
  corroboration: "Corroboration",
  evidence_gaps: "Evidence gaps",
  out_of_scope: "Out of scope",
  disputed_evidence: "Disputed evidence",
  range_cap: "Range cap",
};

function factorLabel(name: string) {
  return FACTOR_LABELS[name] ?? name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/**
 * The score and the factors that composed it.
 *
 * The factors are additive and sum exactly to the total, so the list is a
 * derivation rather than a set of weighted axes — which is why entries can be
 * negative or zero and why their number varies between accounts.
 */
export function ScoreBreakdown({
  score,
  breakdown,
  signals,
}: {
  score: number;
  breakdown?: OpportunityDetail["score_breakdown"];
  signals: ResearchSignal[];
}) {
  const factors = breakdown?.factors ?? [];
  // The stored score is the fallback: it is what an unextracted opportunity has
  // and all it has.
  const headline = breakdown?.total ?? score;
  const scored = signals.filter((s) => s.score !== null && s.score !== undefined);

  return (
    <section className="border-border border-b px-6 py-5">
      <div className="mb-4 flex items-baseline justify-between">
        <SectionLabel>Score breakdown</SectionLabel>
        <span className="font-mono text-[22px] leading-none font-semibold tabular-nums">
          {headline}
        </span>
      </div>

      {factors.length > 0 ? (
        <div className="space-y-3.5">
          {factors.map((factor) => (
            <div key={factor.name}>
              <div className="mb-1.5 flex items-baseline justify-between gap-3">
                <span className="truncate text-[12px]">{factorLabel(factor.name)}</span>
                <span
                  className={cn(
                    "font-mono text-[12px] font-medium tabular-nums",
                    factor.points < 0 && "text-age-overdue",
                    factor.points === 0 && "text-muted-foreground",
                  )}
                >
                  {factor.points > 0 ? `+${factor.points}` : factor.points}
                </span>
              </div>
              {factor.points !== 0 && <ScoreBar value={factor.points} />}
              <p className="text-muted-foreground mt-1 text-[11px] leading-snug">
                {factor.detail}
              </p>
            </div>
          ))}
        </div>
      ) : scored.length > 0 ? (
        // Not yet extracted: the per-signal view is the only breakdown available.
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
            This opportunity predates structured extraction, so the breakdown is
            its signals rather than scoring factors.
          </p>
        </div>
      ) : (
        <p className="text-muted-foreground text-[12px] leading-relaxed">
          No scored signals were recovered from this account&rsquo;s research, so
          there is no breakdown behind this number.
        </p>
      )}
    </section>
  );
}

/**
 * How many signals each list shows before asking.
 *
 * Ranking comes from the API — strongest first, absences last, with a stable
 * tiebreak — so "the first N" is meaningful rather than arbitrary. Capping is
 * a layout decision and stays here; the response keeps every signal because
 * the score composes from all of them.
 */
const RESEARCH_PREVIEW = 3;
const RAIL_PREVIEW = 5;

/** Never a silent cut: the count of what is hidden is always visible, and it
 *  is always one click to see it. */
function MoreToggle({
  hidden,
  expanded,
  onToggle,
  noun = "signal",
}: {
  hidden: number;
  expanded: boolean;
  onToggle: () => void;
  noun?: string;
}) {
  if (hidden <= 0 && !expanded) return null;

  return (
    <button
      type="button"
      onClick={onToggle}
      className="text-muted-foreground hover:text-foreground mt-3 text-[11px] transition-colors duration-75"
    >
      {expanded
        ? "Show fewer"
        : `Show ${hidden} more ${hidden === 1 ? noun : `${noun}s`}`}
    </button>
  );
}

/**
 * A signal's supporting text: the verbatim span, or an explicit statement that
 * there isn't one.
 *
 * Never falls back to the signal's rationale. Rationale is generated prose;
 * substituting it here would present an unverifiable sentence in the place a
 * reader expects quoted source text, which is worse than an empty slot.
 */
function Evidence({ text, label }: { text: string; label?: string }) {
  const body = readable(text ?? "", label);
  if (!body) {
    return (
      <p className="text-muted-foreground text-[12px] leading-[1.65] italic">
        No supporting text was captured for this signal.
      </p>
    );
  }
  return <p className="text-[12px] leading-[1.65]">{body}</p>;
}

/** `rox://contact/<uuid>` refs point into the CRM, not the web — they are
 *  traceability, not something a reader can open. */
function SourceLinks({ sources }: { sources: SourceRef[] }) {
  const web = sources.filter((s) => s.kind === "web");
  if (web.length === 0) return null;

  return (
    <span className="flex items-center gap-1.5">
      {web.map((source) => (
        <a
          key={source.url}
          href={source.url}
          target="_blank"
          rel="noreferrer noopener"
          title={source.url}
          className="text-muted-foreground hover:text-foreground inline-flex max-w-[120px] items-center gap-1 text-[10px] transition-colors duration-75"
        >
          <span className="truncate">{hostname(source.url)}</span>
          <ExternalLink size={9} strokeWidth={2} aria-hidden />
        </a>
      ))}
    </span>
  );
}

function hostname(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/**
 * `[\[1\]](https://…)` footnotes, and bare `[\[1\]]` markers.
 *
 * Evidence is stored verbatim so the citation URLs can be recovered from it and
 * checked against the artifact's known sources. That makes the markup part of
 * the text rather than something the server strips, so it comes off here — the
 * same URLs are already rendered as links beside the heading.
 */
const CITATION = /\[\\?\[\d+\\?\]\](?:\([^)]*\))?/g;

/** `… found. Confidence: 9.` — the model restating its own field inside the
 *  span. It is metadata, and the confidence is already shown beside the label. */
const TRAILING_CONFIDENCE = /[\s.;—-]*\(?\s*confidence[^.\n]{0,40}\)?\s*\.?\s*$/i;

const alpha = (s: string) => s.toLowerCase().replace(/[^a-z]/g, "");

/**
 * Drop a leading restatement of the signal's own category.
 *
 * "Growth / Expansion signals (headcount, hiring, funding): No account-level
 * evidence found" duplicates the label rendered directly above it.
 *
 * Matched against *this signal's label* rather than a list of category names.
 * A vocabulary regex here would need extending every time the wording drifts,
 * which is exactly how the old parser became unmaintainable — and it would
 * happily strip a leading clause that merely resembled a category.
 */
function stripLabelPrefix(text: string, label?: string) {
  if (!label) return text;
  const colon = text.indexOf(":");
  if (colon < 0 || colon > 90) return text;

  const head = alpha(
    text
      .slice(0, colon)
      .replace(/\(.*?\)/g, "") // parenthetical enumerations of what was looked for
      .replace(/\b(evidence of|signals?|events?|mentions?|indicators?)\b/gi, ""),
  );
  const want = alpha(label);
  if (!head || !want) return text;
  if (!(head === want || head.includes(want) || want.includes(head))) return text;

  // The parenthetical in a prefix enumerates what research looked for
  // ("(incumbent dissatisfaction, RFPs, replacement)"), which is worth keeping.
  // When the remainder is this short the prefix was carrying the substance —
  // "no evidence found." on its own says less than the heading already does —
  // so the mild duplication beats the loss.
  const rest = text.slice(colon + 1).trim();
  return rest.length >= 40 ? rest : text;
}

/** HTML tags. React escapes them, so they reach the page as a literal `<br>`
 *  and read as broken output. Never legitimate in research prose. */
const TAGS = /<\/?[a-z][^>]*>/gi;

/**
 * Structural debris that does not belong in prose: brace/quote runs and
 * `key: value` or `key=value` fragments posing as structured fields.
 *
 * This flags, it does not scrub. Research text is third-party and this data
 * contains at least three payload shapes — `','is_absence':true,'x':0}`,
 * `,is_absence=true`, and `I"x":0}}"}}}`. A regex that removed some and missed
 * others would leave the page looking clean while the rest still rendered,
 * which is worse than showing all of it and saying the source is malformed.
 *
 * The controls that matter are upstream: the extraction prompt treats
 * narrative text as data rather than instruction, and `contests_absence`
 * checks the model against its own evidence. This is the reviewer's warning
 * that the underlying cell is not trustworthy prose.
 */
const MALFORMED = /<\/?[a-z][^>]*>|[{}]{2,}|['"][a-z_]{2,}['"]?\s*[:=]\s*(true|false|\d)/i;

function looksMalformed(text: string) {
  return MALFORMED.test(text ?? "");
}

function readable(text: string, label?: string) {
  return stripLabelPrefix(text.replace(CITATION, ""), label)
    .replace(TAGS, " ")
    .replace(TRAILING_CONFIDENCE, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\s+([.,;])/g, "$1")
    .trim();
}

/**
 * Absence signals quoting the same span, collapsed into one row.
 *
 * One "nothing was found" sentence legitimately becomes one absence per
 * category — that is the extraction prompt working as written, and the stored
 * signals must keep their own types because scoring counts them separately.
 * But this section renders only the evidence, so six typed absences over one
 * sentence printed as six byte-identical blocks.
 *
 * Grouping is display-only: every signal still arrives, still scores, and is
 * still reachable here. Only the row count drops.
 *
 * Positives never group — two findings that happen to cite one sentence are
 * still two findings. Contested signals never group either: a contested signal
 * is one the model called positive over absence-shaped evidence, so folding it
 * into an absence block would assert the very thing the flag disputes.
 */
function groupRows(signals: ExtractedSignal[]): ExtractedSignal[][] {
  const rows: ExtractedSignal[][] = [];
  const byEvidence = new Map<string, ExtractedSignal[]>();

  for (const signal of signals) {
    const span = (signal.evidence ?? "").trim();
    // An empty span carries nothing to group on — two signals whose evidence
    // failed validation are not thereby the same finding.
    if (!signal.is_absence || signal.contested || !span) {
      rows.push([signal]);
      continue;
    }

    const existing = byEvidence.get(span);
    if (existing) {
      existing.push(signal);
      continue;
    }
    // Pushed and indexed as the same array, so later members land in the row
    // already holding this span's position in the ranking.
    const created = [signal];
    byEvidence.set(span, created);
    rows.push(created);
  }

  return rows;
}

/**
 * Extracted signals as labelled sections, each with the citations backing it.
 *
 * Falls back to the artifact's parsed signals, then to its narrative prose, for
 * opportunities whose research predates structured extraction.
 */
export function ResearchSummary({
  research,
  extractedSignals = [],
}: {
  research: OpportunityDetail["research"];
  extractedSignals?: ExtractedSignal[];
}) {
  const [expanded, setExpanded] = useState(false);

  if (extractedSignals.length > 0) {
    const fetchedAt = (research ?? [])[0]?.fetched_at;
    const columnName = (research ?? [])[0]?.column_name;
    // `other` is the dismissed bucket — firmographics and reporting cadence —
    // and scores nothing by definition. Left in server rank it fills the whole
    // preview: Cisco's top three were a headquarters address, a website and an
    // industry description, with every real finding behind the toggle.
    //
    // Deranked here rather than filtered: this section is the one place that
    // lists every finding, and the rail beside it already drops `other`.
    // A stable sort on that single key leaves the server's ranking — absence,
    // then confidence, then emitted position — intact underneath, so display
    // order stays a strict refinement of the order scoring and the brief read.
    // Copied first because the array belongs to the query cache.
    const ranked = [...extractedSignals].sort(
      (a, b) => Number(a.signal_type === "other") - Number(b.signal_type === "other"),
    );
    const rows = groupRows(ranked);
    const shown = expanded ? rows : rows.slice(0, RESEARCH_PREVIEW);

    return (
      <section className="px-6 py-5">
        <SectionLabel>Research summary</SectionLabel>
        <div className="space-y-5">
          {shown.map((row) => {
            // Every member of a group quotes the same span, and sources are
            // resolved from that span, so the lead's are the whole row's.
            const [lead] = row;
            const sources = lead.sources ?? [];
            const label = row.map((signal) => signal.label).join(" · ");

            return (
              <article key={lead.id} className="border-border border-b pb-5 last:border-0 last:pb-0">
                <div className="mb-1.5 flex items-center justify-between gap-3">
                  <span className="flex min-w-0 items-center gap-1.5">
                    {/* Six categories over one span outruns the column, so the
                        full list stays reachable on hover. */}
                    <span className="truncate text-[12px] font-medium" title={label}>
                      {label}
                    </span>
                    {lead.is_absence && (
                      <span className="text-muted-foreground shrink-0 text-[10px]">
                        nothing found
                      </span>
                    )}
                    {lead.contested && (
                      <span className="text-age-overdue shrink-0 text-[10px]">disputed</span>
                    )}
                    {looksMalformed(lead.evidence) && (
                      <span
                        className="text-age-overdue shrink-0 text-[10px]"
                        title="The source research contains markup or structured-data fragments. Read this evidence with care."
                      >
                        malformed source
                      </span>
                    )}
                  </span>
                  <div className="text-muted-foreground flex shrink-0 items-center gap-1.5 text-[10px]">
                    <SourceLinks sources={sources} />
                    {fetchedAt && (
                      <>
                        {sources.some((s) => s.kind === "web") && (
                          <span className="text-border">·</span>
                        )}
                        <span className="font-mono" title={absoluteTime(fetchedAt)}>
                          {formatAge(fetchedAt)} ago
                        </span>
                      </>
                    )}
                  </div>
                </div>
                {/* The verbatim span only. The per-signal rationale is generated
                    prose and cannot be checked against the source, so it is not
                    shown here — evidence can be, and is. */}
                {/* A grouped row passes no label: `readable` strips a leading
                    category prefix, and only the lead's would ever match. */}
                <Evidence
                  text={lead.evidence}
                  label={row.length === 1 ? lead.label : undefined}
                />
              </article>
            );
          })}
        </div>
        <MoreToggle
          hidden={rows.length - shown.length}
          expanded={expanded}
          onToggle={() => setExpanded((v) => !v)}
          noun="finding"
        />
        {columnName && (
          <p className="text-muted-foreground/70 mt-4 text-[10px]">Source: {columnName}</p>
        )}
      </section>
    );
  }

  return <LegacyResearchSummary research={research} />;
}

/** The pre-extraction rendering: parsed signals, or narrative prose. */
function LegacyResearchSummary({ research }: { research: OpportunityDetail["research"] }) {
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
 * The signals research found, strongest first.
 *
 * Absences are shown rather than filtered: "we checked buying intent and found
 * nothing" is a finding a reviewer needs, and it is the most common single
 * outcome in the data. They are visually demoted so they cannot be misread as
 * evidence of opportunity.
 */
export function RecentSignals({ signals }: { signals: ExtractedSignal[] }) {
  const [expanded, setExpanded] = useState(false);

  // `other` is the dismissed bucket — firmographics and reporting cadence. It
  // scores nothing and reads as filler in a summary panel; the research section
  // below still lists every finding.
  const relevant = signals.filter((s) => s.signal_type !== "other");

  if (relevant.length === 0 && signals.length > 0) {
    return (
      <section className="border-border border-b px-5 py-5">
        <SectionLabel>Signals</SectionLabel>
        <p className="text-muted-foreground text-[12px] leading-relaxed">
          Research returned only out-of-category context for this account — no
          growth, intent, timing, engagement, competitive or whitespace signals.
        </p>
      </section>
    );
  }

  if (signals.length === 0) {
    return (
      <section className="border-border border-b px-5 py-5">
        <SectionLabel>Signals</SectionLabel>
        <p className="text-muted-foreground text-[12px] leading-relaxed">
          This account&rsquo;s research has not been through structured
          extraction yet, so there are no individual signals to list.
        </p>
      </section>
    );
  }

  const shown = expanded ? relevant : relevant.slice(0, RAIL_PREVIEW);

  return (
    <section className="border-border border-b px-5 py-5">
      <SectionLabel>Signals</SectionLabel>
      <ul className="space-y-2.5">
        {shown.map((signal) => {
          const muted = signal.is_absence || signal.contested;
          return (
            <li key={signal.id} className="flex items-start gap-2">
              <span
                className={cn(
                  "mt-[5px] size-[7px] shrink-0 rounded-full border",
                  muted
                    ? "border-border bg-background"
                    : signal.confidence >= 8
                      ? "border-foreground bg-foreground"
                      : signal.confidence >= 6
                        ? "border-muted-foreground bg-muted-foreground"
                        : "border-muted-foreground bg-background",
                )}
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                {/* The evidence span, not the rationale — four findings can
                    share one category, so the label alone would repeat with
                    nothing to tell them apart, and generated prose is not the
                    way to differentiate them. Clamped because this rail is an
                    index; the full span is in the research section, untruncated. */}
                <p
                  className={cn(
                    "line-clamp-2 text-[12px] leading-snug",
                    muted && "text-muted-foreground",
                  )}
                  title={readable(signal.evidence, signal.label) || undefined}
                >
                  {readable(signal.evidence, signal.label) || signal.label}
                </p>
                <p className="text-muted-foreground mt-0.5 text-[10px] leading-none">
                  {signal.label}
                  <span className="text-border mx-1">·</span>
                  {signal.is_absence
                    ? "nothing found"
                    : signal.contested
                      ? "disputed, withheld"
                      : `confidence ${signal.confidence}/10`}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
      <MoreToggle
        hidden={relevant.length - shown.length}
        expanded={expanded}
        onToggle={() => setExpanded((v) => !v)}
      />
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

/** What happened to this opportunity, newest first. */
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
