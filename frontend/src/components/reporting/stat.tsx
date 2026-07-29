import { cn } from "@/lib/utils";

/**
 * One number with its context. `sub` is where a denominator lives — the page
 * rule is that no rate renders bare, so a Stat showing a percentage should
 * always receive one ("8 of 11 decided", "18 of 20 runs").
 */
export function Stat({
  value,
  sub,
  label,
  muted = false,
  size = "md",
}: {
  value: string;
  /** Denominator or unit, rendered beside the value. */
  sub?: string;
  label: string;
  /** De-emphasized variant, for secondary framings of the same fact. */
  muted?: boolean;
  size?: "md" | "lg";
}) {
  return (
    <div>
      <div className="flex items-baseline gap-1.5">
        <span
          className={cn(
            "font-mono leading-none font-semibold tabular-nums",
            size === "lg" ? "text-[22px]" : "text-[16px]",
            muted ? "text-foreground/50" : "text-foreground",
          )}
        >
          {value}
        </span>
        {sub && (
          <span className={cn("text-[10px]", muted ? "text-muted-foreground/70" : "text-muted-foreground")}>
            {sub}
          </span>
        )}
      </div>
      <p
        className={cn(
          "mt-1 text-[11px] leading-snug",
          muted ? "text-muted-foreground/60" : "text-muted-foreground",
        )}
      >
        {label}
      </p>
    </div>
  );
}
