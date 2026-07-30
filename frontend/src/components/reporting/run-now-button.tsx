"use client";

import { Play, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

import { useRunHealth, useTriggerRun } from "./use-reporting";

/**
 * Start a forced research cycle from the UI.
 *
 * Two-step arm/confirm instead of a dialog: a single misclick must not start
 * an ~11-minute run that ends in a real email, but a modal is more ceremony
 * than one deliberate second click. Arming times out on its own.
 *
 * While a run is live the button becomes the progress indicator — run-health
 * polls every 5s while its top row reports "running", so the elapsed time
 * ticks without this component owning a timer beyond its display tick.
 */
const ARM_TIMEOUT_MS = 4_000;

export function RunNowButton() {
  const health = useRunHealth();
  const trigger = useTriggerRun();
  const [armed, setArmed] = useState(false);

  const topRun = health.data?.recent_runs?.[0];
  const running = topRun?.status === "running";

  // The clock lives in state and moves only inside the effect — an inline
  // Date.now() during render is an impure read the compiler rejects. Ticking
  // once a second keeps the elapsed label moving between run-health polls.
  const [now, setNow] = useState(() => Date.now());
  // No synchronous set in the effect body — the first interval tick corrects
  // the mount-time value within a second, and Math.max clamps until then.
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(id);
  }, [running]);

  useEffect(() => {
    if (!armed) return;
    const id = setTimeout(() => setArmed(false), ARM_TIMEOUT_MS);
    return () => clearTimeout(id);
  }, [armed]);

  if (running) {
    const elapsed = Math.max(0, now - Date.parse(topRun.started_at));
    const minutes = Math.floor(elapsed / 60_000);
    const seconds = Math.floor((elapsed % 60_000) / 1_000);
    return (
      <span
        className="border-border text-muted-foreground inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-medium"
        title="A research cycle is in flight — see Automation health below"
      >
        <RefreshCw size={11} strokeWidth={2} className="animate-spin" aria-hidden />
        Run in progress · {minutes}m {String(seconds).padStart(2, "0")}s
      </span>
    );
  }

  const label = trigger.isPending
    ? "Starting…"
    : armed
      ? "Click again to start (~11 min)"
      : "Run research now";

  return (
    <button
      type="button"
      disabled={trigger.isPending}
      onClick={() => {
        if (!armed) {
          setArmed(true);
          return;
        }
        setArmed(false);
        trigger.mutate();
      }}
      className={cn(
        "focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5",
        "text-[12px] font-medium transition-colors focus-visible:ring-1 focus-visible:outline-none",
        "disabled:opacity-40",
        armed
          ? "border-foreground bg-foreground text-background"
          : "border-border bg-card hover:bg-accent",
      )}
      title="Forced cycle: re-extracts research and waives the decision cooldown. Ends with a digest email if anything qualifies."
    >
      <Play size={11} strokeWidth={2} aria-hidden />
      {label}
    </button>
  );
}
