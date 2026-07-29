"use client";

import { flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { ArrowDown, ArrowUp } from "lucide-react";

import { cn } from "@/lib/utils";

import { pipelineColumns, type PipelineColumnMeta } from "./columns";
import type { Opportunity } from "./use-opportunities";

export interface SortState {
  sort: string;
  order: "asc" | "desc";
}

export function PipelineTable({
  rows,
  sortState,
  onSortChange,
}: {
  rows: Opportunity[];
  sortState: SortState;
  onSortChange: (next: SortState) => void;
}) {
  // TanStack Table returns functions the React Compiler cannot safely memoize,
  // so it skips this component either way. Opting out explicitly makes that
  // intentional rather than inferred — and keeps the build output honest.
  "use no memo";

  // eslint-disable-next-line react-hooks/incompatible-library -- opted out above
  const table = useReactTable({
    data: rows,
    columns: pipelineColumns,
    getCoreRowModel: getCoreRowModel(),
    // Sorting is the server's job: it spans the whole result set, not just the
    // page in hand, and the API already orders stage by funnel position.
    manualSorting: true,
  });

  return (
    <div className="overflow-x-auto">
      <table className="w-full table-fixed border-collapse">
        <thead className="bg-background sticky top-0 z-10">
          <tr>
            {table.getFlatHeaders().map((header) => {
              const meta = header.column.columnDef.meta as PipelineColumnMeta | undefined;
              const sortKey = meta?.sortKey;
              const active = sortKey === sortState.sort;

              return (
                <th
                  key={header.id}
                  scope="col"
                  aria-sort={
                    active
                      ? sortState.order === "asc"
                        ? "ascending"
                        : "descending"
                      : undefined
                  }
                  className={cn(
                    "border-border text-muted-foreground border-b px-3 py-2",
                    "text-[11px] font-medium tracking-wider uppercase",
                    meta?.align === "right" ? "text-right" : "text-left",
                    meta?.headerClassName,
                  )}
                >
                  {sortKey ? (
                    <button
                      type="button"
                      onClick={() =>
                        onSortChange({
                          sort: sortKey,
                          // re-picking the active column flips direction;
                          // a new column starts descending, which puts the
                          // most urgent rows first for score and age
                          order: active && sortState.order === "desc" ? "asc" : "desc",
                        })
                      }
                      className={cn(
                        "hover:text-foreground inline-flex items-center gap-1 transition-colors",
                        "focus-visible:ring-ring rounded-sm focus-visible:ring-1 focus-visible:outline-none",
                        meta?.align === "right" && "flex-row-reverse",
                        active && "text-foreground",
                      )}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {active &&
                        (sortState.order === "desc" ? (
                          <ArrowDown size={11} strokeWidth={2.5} aria-hidden />
                        ) : (
                          <ArrowUp size={11} strokeWidth={2.5} aria-hidden />
                        ))}
                    </button>
                  ) : (
                    flexRender(header.column.columnDef.header, header.getContext())
                  )}
                </th>
              );
            })}
          </tr>
        </thead>

        <tbody className="bg-card">
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              tabIndex={0}
              className={cn(
                "border-border hover:bg-accent/40 border-b transition-colors last:border-0",
                "focus-visible:bg-accent/40 focus-visible:outline-none",
              )}
            >
              {row.getVisibleCells().map((cell) => {
                const meta = cell.column.columnDef.meta as PipelineColumnMeta | undefined;
                return (
                  <td
                    key={cell.id}
                    className={cn("py-2 align-top text-[12px]", meta?.cellClassName)}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
