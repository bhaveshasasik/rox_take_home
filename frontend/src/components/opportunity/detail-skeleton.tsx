import { Skeleton } from "@/components/ui/skeleton";

/**
 * Mirrors the real two-column layout so nothing shifts when data lands —
 * same header, same left/right split, same section rhythm.
 */
export function DetailSkeleton() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading opportunity…</span>

      <header className="border-border bg-card border-b px-6 py-4">
        <Skeleton className="h-[15px] w-72" />
        <Skeleton className="mt-2 h-[12px] w-96" />
      </header>

      <div className="flex">
        <div className="border-border min-w-0 flex-1 border-r">
          <section className="border-border border-b px-6 py-5">
            <Skeleton className="mb-3 h-[10px] w-24" />
            <Skeleton className="h-[13px] w-full" />
            <Skeleton className="mt-2 h-[13px] w-[92%]" />
            <Skeleton className="mt-2 h-[13px] w-[70%]" />
          </section>

          <section className="border-border border-b px-6 py-5">
            <Skeleton className="mb-4 h-[10px] w-28" />
            {[0, 1, 2].map((row) => (
              <div key={row} className="mb-3.5">
                <Skeleton className="mb-1.5 h-[12px] w-40" />
                <Skeleton className="h-1 w-full rounded-full" />
              </div>
            ))}
          </section>

          <section className="px-6 py-5">
            <Skeleton className="mb-3 h-[10px] w-32" />
            {[0, 1].map((row) => (
              <div key={row} className="mb-5">
                <Skeleton className="mb-1.5 h-[12px] w-48" />
                <Skeleton className="h-[12px] w-full" />
                <Skeleton className="mt-1.5 h-[12px] w-[85%]" />
              </div>
            ))}
          </section>
        </div>

        <div className="w-72 shrink-0">
          <section className="border-border border-b px-5 py-5">
            <Skeleton className="mb-3 h-[10px] w-20" />
            {[0, 1, 2, 3].map((row) => (
              <div key={row} className="mb-2 flex justify-between gap-3">
                <Skeleton className="h-[11px] w-20" />
                <Skeleton className="h-[11px] w-24" />
              </div>
            ))}
          </section>
        </div>
      </div>
    </div>
  );
}
