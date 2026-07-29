"use client";

import type { ColumnDef } from "@tanstack/react-table";
import Link from "next/link";

import { formatAge, humanizeStage, isAging, pipelineStatus } from "@/lib/pipeline";
import { cn } from "@/lib/utils";

import { StatusPill } from "./status-pill";
import type { Opportunity } from "./use-opportunities";

/**
 * Fixed widths, applied with `table-fixed`. Auto-sizing columns look fine
 * against tidy mock data and fall apart the moment a real account name is
 * "Continental Freight & Logistics Partners LLC" — the column steals space
 * from every neighbour instead of truncating.
 *
 * `sortKey` is the value the API's `sort` param expects; a column without one
 * is not sortable server-side and its header stays inert.
 */
export interface PipelineColumnMeta {
  headerClassName?: string;
  cellClassName?: string;
  align?: "left" | "right";
  sortKey?: string;
}

const NO_VALUE = "—";

/**
 * The design shows an Owner column. It is deliberately absent: `assigned_to`
 * is declared in the schema but never written by the backend, so the column
 * would be empty on every row. A structurally blank column is worse than none.
 */
export const pipelineColumns: ColumnDef<Opportunity, unknown>[] = [
  {
    id: "account",
    header: "Account",
    accessorFn: (row) => row.account_name ?? row.account_id,
    meta: {
      sortKey: "account",
      headerClassName: "w-[36%] pl-6",
      cellClassName: "pl-6 pr-3",
    } satisfies PipelineColumnMeta,
    cell: ({ row }) => {
      const { id, account_name, title } = row.original;
      return (
        <div className="min-w-0">
          {/* account_name is optional *and* nullable in the spec — it comes
              from a join that can miss. Fall back rather than render "null". */}
          <Link
            href={`/opportunities/${id}`}
            className="hover:underline focus-visible:ring-ring block truncate rounded-sm text-[13px] leading-tight font-medium focus-visible:ring-1 focus-visible:outline-none"
            title={account_name ?? undefined}
          >
            {account_name ?? <span className="text-muted-foreground">Unknown account</span>}
          </Link>
          <div
            className="text-muted-foreground mt-0.5 truncate text-[11px] leading-tight"
            title={title}
          >
            {title}
          </div>
        </div>
      );
    },
  },
  {
    id: "signal",
    header: "Signal",
    accessorFn: (row) => row.signal_label ?? row.signal_type,
    meta: {
      sortKey: "signal_type",
      headerClassName: "w-[24%]",
      cellClassName: "px-3",
    } satisfies PipelineColumnMeta,
    cell: ({ row }) => {
      const { signal_label, signal_type } = row.original;
      return (
        <div className="truncate text-[12px]" title={signal_label ?? signal_type}>
          {signal_label ?? signal_type}
        </div>
      );
    },
  },
  {
    id: "score",
    header: "Score",
    accessorFn: (row) => row.qualification_score,
    meta: {
      sortKey: "score",
      align: "right",
      headerClassName: "w-[88px] text-right",
      cellClassName: "px-3 text-right",
    } satisfies PipelineColumnMeta,
    cell: ({ row }) => <ScoreCell score={row.original.qualification_score} />,
  },
  {
    id: "age",
    header: "Age",
    accessorFn: (row) => row.created_at,
    meta: {
      sortKey: "created_at",
      align: "right",
      headerClassName: "w-[80px] text-right",
      cellClassName: "px-3 text-right",
    } satisfies PipelineColumnMeta,
    cell: ({ row }) => {
      const { created_at } = row.original;
      const aging = isAging(created_at);
      return (
        <span
          className={cn(
            "font-mono text-[12px] tabular-nums",
            // the second and last place colour carries meaning
            aging ? "text-age-overdue font-medium" : "text-muted-foreground",
          )}
          title={new Date(created_at).toLocaleString()}
        >
          {formatAge(created_at)}
        </span>
      );
    },
  },
  {
    id: "status",
    header: "Status",
    accessorFn: (row) => row.status,
    meta: {
      sortKey: "status",
      align: "right",
      headerClassName: "w-[150px] pr-6 text-right",
      cellClassName: "pl-3 pr-6 text-right",
    } satisfies PipelineColumnMeta,
    cell: ({ row }) => {
      const { status, stage, needs_review } = row.original;
      return (
        <div className="flex flex-col items-end gap-1">
          <StatusPill status={pipelineStatus(status, stage)} />
          {/* All three status fields are shown. Stage is the finer position and
              needs_review is a separate flag, so neither is implied by the
              pill — but both stay neutral, since colour is reserved. */}
          <span className="text-muted-foreground truncate text-[10px] leading-none">
            {humanizeStage(stage)}
            {needs_review ? " · needs review" : ""}
          </span>
        </div>
      );
    },
  },
];

/**
 * Typed as nullable even though the spec has `qualification_score` required
 * and non-null, so an unscored row can never render as a bare `0` or `null`
 * if that ever changes. Today the dash is unreachable through the API.
 */
function ScoreCell({ score }: { score: number | null }) {
  if (score === null) {
    return <span className="text-muted-foreground font-mono text-[12px]">{NO_VALUE}</span>;
  }
  return <span className="font-mono text-[12px] tabular-nums">{score}</span>;
}
