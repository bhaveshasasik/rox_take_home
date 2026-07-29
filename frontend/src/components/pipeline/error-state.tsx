import { AlertTriangle, RefreshCw } from "lucide-react";

import { ApiError } from "@/api/client";

/**
 * Says what failed and offers a way back. The design mocks up a request id
 * (`req 7f3a91c`); the API doesn't return one, so the status code and the
 * server's own `detail` message are shown instead of inventing an identifier.
 */
export function ErrorState({
  error,
  onRetry,
  isRetrying,
}: {
  error: unknown;
  onRetry: () => void;
  isRetrying: boolean;
}) {
  const { headline, detail } = describe(error);

  return (
    <div className="bg-card border-border flex flex-col items-center justify-center border-t px-6 py-20">
      <div className="bg-status-rejected mb-5 flex size-10 items-center justify-center rounded-lg">
        <AlertTriangle
          size={18}
          strokeWidth={1.5}
          className="text-status-rejected-fg"
          aria-hidden
        />
      </div>

      <p className="mb-1 text-[13px] font-medium">Failed to load pipeline</p>
      <p className="text-muted-foreground max-w-[320px] text-center text-[12px] leading-relaxed">
        {headline}
      </p>
      {detail && (
        <p className="text-muted-foreground/70 mt-1 font-mono text-[11px]">{detail}</p>
      )}

      <button
        type="button"
        onClick={onRetry}
        disabled={isRetrying}
        className="border-border bg-card hover:bg-accent focus-visible:ring-ring mt-5 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none disabled:opacity-40"
      >
        <RefreshCw
          size={11}
          strokeWidth={2}
          className={isRetrying ? "animate-spin" : undefined}
          aria-hidden
        />
        {isRetrying ? "Retrying…" : "Try again"}
      </button>
    </div>
  );
}

function describe(error: unknown): { headline: string; detail?: string } {
  if (error instanceof ApiError) {
    return {
      headline:
        error.status >= 500
          ? "The pipeline service returned an error. This is usually temporary."
          : "The request was rejected.",
      // `detail` is FastAPI's own message, already flattened by ApiError —
      // never a raw exception or stack trace.
      detail: `HTTP ${error.status}${error.detail ? ` · ${String(error.detail)}` : ""}`,
    };
  }
  if (error instanceof Error && error.message) {
    return { headline: "Couldn't reach the pipeline service.", detail: error.message };
  }
  return { headline: "Something went wrong loading the pipeline." };
}
