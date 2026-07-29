"use client";

import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ExternalLink, Mail } from "lucide-react";

import { Pill, type PillTone } from "@/components/pipeline/status-pill";
import { TABLE_CELL, TABLE_HEAD, TABLE_ROW } from "@/components/pipeline/table-chrome";
import { cn } from "@/lib/utils";

import type { Enrollment } from "./use-prospecting";

const ENROLLMENT_TONES: Record<Enrollment["status"], PillTone> = {
  pending: "neutral",
  active: "info",
  replied: "positive",
  completed: "positive",
  bounced: "negative",
};

interface ColumnMeta {
  headerClassName?: string;
  cellClassName?: string;
}

/**
 * Contacts prospecting selected, with the reason each was chosen.
 *
 * Fixed widths and `table-fixed` for the same reason as the pipeline list:
 * `match_reason` is free text and would otherwise starve every other column.
 * `contact.score` exists in the spec but is deliberately not shown — it is
 * null for half the live contacts, and relative match quality is not what a
 * reviewer acts on here.
 */
function buildColumns(onViewEmails: (enrollment: Enrollment) => void) {
  const columns: ColumnDef<Enrollment, unknown>[] = [
    {
      id: "contact",
      header: "Contact",
      accessorFn: (row) => row.contact.name,
      meta: { headerClassName: "w-[24%] pl-6 text-left", cellClassName: "pl-6 pr-3" },
      cell: ({ row }) => {
        const { name, title } = row.original.contact;
        return (
          <div className="min-w-0">
            <div className="truncate text-[13px] leading-tight font-medium" title={name}>
              {name}
            </div>
            <div
              className="text-muted-foreground mt-0.5 truncate text-[11px] leading-tight"
              title={title ?? undefined}
            >
              {/* optional and nullable in the spec */}
              {title ?? "Title unknown"}
            </div>
          </div>
        );
      },
    },
    {
      id: "reach",
      header: "Reach",
      accessorFn: (row) => row.contact.email ?? "",
      meta: { headerClassName: "w-[20%] text-left", cellClassName: "px-3" },
      cell: ({ row }) => {
        const { email, linkedin_url } = row.original.contact;
        if (!email && !linkedin_url) {
          return <span className="text-muted-foreground">—</span>;
        }
        return (
          <div className="flex min-w-0 flex-col gap-1">
            {email && (
              <a
                href={`mailto:${email}`}
                title={email}
                className="focus-visible:ring-ring flex min-w-0 items-center gap-1.5 rounded-sm hover:underline focus-visible:ring-1 focus-visible:outline-none"
              >
                <Mail size={11} strokeWidth={2} aria-hidden className="shrink-0" />
                <span className="truncate">{email}</span>
              </a>
            )}
            {linkedin_url && (
              <a
                href={linkedin_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-muted-foreground hover:text-foreground focus-visible:ring-ring flex items-center gap-1.5 rounded-sm text-[11px] transition-colors hover:underline focus-visible:ring-1 focus-visible:outline-none"
              >
                <ExternalLink size={11} strokeWidth={2} aria-hidden className="shrink-0" />
                <span>LinkedIn</span>
              </a>
            )}
          </div>
        );
      },
    },
    {
      id: "why",
      header: "Why selected",
      accessorFn: (row) => row.contact.match_reason ?? "",
      meta: { headerClassName: "w-[34%] text-left", cellClassName: "px-3" },
      cell: ({ row }) => {
        const reason = row.original.contact.match_reason;
        return reason ? (
          <p className="leading-snug" title={reason}>
            {reason}
          </p>
        ) : (
          <span className="text-muted-foreground">No reason recorded</span>
        );
      },
    },
    {
      id: "status",
      header: "Status",
      accessorFn: (row) => row.status,
      meta: { headerClassName: "w-[110px] text-left", cellClassName: "px-3" },
      cell: ({ row }) => (
        <Pill tone={ENROLLMENT_TONES[row.original.status]}>{row.original.status}</Pill>
      ),
    },
    {
      id: "emails",
      header: "Emails",
      accessorFn: (row) => row.emails?.length ?? 0,
      meta: { headerClassName: "w-[120px] pr-6 text-right", cellClassName: "pr-6 pl-3 text-right" },
      cell: ({ row }) => {
        const count = row.original.emails?.length ?? 0;
        if (count === 0) return <span className="text-muted-foreground">—</span>;
        return (
          <button
            type="button"
            onClick={() => onViewEmails(row.original)}
            className="border-border bg-card hover:bg-accent focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none"
          >
            View {count}
          </button>
        );
      },
    },
  ];
  return columns;
}

export function ContactsTable({
  enrollments,
  onViewEmails,
}: {
  enrollments: Enrollment[];
  onViewEmails: (enrollment: Enrollment) => void;
}) {
  // See the note in pipeline-table.tsx — same TanStack/React Compiler opt-out.
  "use no memo";

  // eslint-disable-next-line react-hooks/incompatible-library -- opted out above
  const table = useReactTable({
    data: enrollments,
    columns: buildColumns(onViewEmails),
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="overflow-x-auto">
      <table className="w-full table-fixed border-collapse">
        <thead className="bg-background">
          <tr>
            {table.getFlatHeaders().map((header) => {
              const meta = header.column.columnDef.meta as ColumnMeta | undefined;
              return (
                <th key={header.id} scope="col" className={cn(TABLE_HEAD, meta?.headerClassName)}>
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className="bg-card">
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className={TABLE_ROW}>
              {row.getVisibleCells().map((cell) => {
                const meta = cell.column.columnDef.meta as ColumnMeta | undefined;
                return (
                  <td key={cell.id} className={cn(TABLE_CELL, meta?.cellClassName)}>
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
