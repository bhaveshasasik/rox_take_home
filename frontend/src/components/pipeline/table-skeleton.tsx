import { Skeleton } from "@/components/ui/skeleton";

import { pipelineColumns, type PipelineColumnMeta } from "./columns";

/**
 * Deliberately not a spinner. It reuses the real column definitions, so the
 * header row and every column width are identical to the loaded table and
 * nothing shifts when data arrives — layout jump on load is most of what
 * makes an app feel cheap.
 */
export function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="overflow-x-auto" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading opportunities…</span>
      <table className="w-full table-fixed border-collapse">
        <thead className="bg-background">
          <tr>
            {pipelineColumns.map((column) => {
              const meta = column.meta as PipelineColumnMeta | undefined;
              return (
                <th
                  key={column.id}
                  scope="col"
                  className={[
                    "border-border text-muted-foreground border-b px-3 py-2",
                    "text-[11px] font-medium tracking-wider uppercase",
                    meta?.align === "right" ? "text-right" : "text-left",
                    meta?.headerClassName ?? "",
                  ].join(" ")}
                >
                  {typeof column.header === "string" ? column.header : null}
                </th>
              );
            })}
          </tr>
        </thead>

        <tbody className="bg-card">
          {Array.from({ length: rows }, (_, rowIndex) => (
            <tr key={rowIndex} className="border-border border-b last:border-0">
              <td className="py-2 pr-3 pl-6 align-top">
                <Skeleton className="h-[13px] w-[70%]" />
                <Skeleton className="mt-1.5 h-[11px] w-[45%]" />
              </td>
              <td className="px-3 py-2 align-top">
                <Skeleton className="h-[13px] w-[75%]" />
              </td>
              <td className="px-3 py-2 align-top">
                <Skeleton className="ml-auto h-[13px] w-6" />
              </td>
              <td className="px-3 py-2 align-top">
                <Skeleton className="ml-auto h-[13px] w-6" />
              </td>
              <td className="py-2 pr-6 pl-3 align-top">
                <Skeleton className="ml-auto h-[17px] w-16 rounded-sm" />
                <Skeleton className="mt-1.5 ml-auto h-[10px] w-20" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
