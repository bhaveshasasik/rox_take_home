"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { REASON_CODES, REASON_LABELS, type DecisionInput } from "./use-opportunity";
import type { Schemas } from "@/api/client";

export function RejectDialog({
  open,
  onOpenChange,
  accountName,
  onConfirm,
  isPending,
  error,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  accountName: string;
  onConfirm: (input: DecisionInput) => void;
  isPending: boolean;
  error: unknown;
}) {
  const [reason, setReason] = useState<Schemas["ReasonCode"] | "">("");
  const [notes, setNotes] = useState("");

  // State deliberately lives here and is only cleared on a *successful* close.
  // Resetting on every close would discard what the user typed the moment a
  // request fails, which is exactly when they least want to retype it.
  function handleOpenChange(next: boolean) {
    if (!next && isPending) return; // don't let a dismiss orphan an in-flight request
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[400px]" showCloseButton={!isPending}>
        <DialogHeader>
          <DialogTitle className="text-[13px] font-semibold">Reject opportunity</DialogTitle>
          <DialogDescription className="text-muted-foreground text-[11px]">
            {accountName}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label htmlFor="reject-reason" className="mb-1.5 text-[11px] font-medium">
              Reason <span className="text-age-overdue">*</span>
            </Label>
            <div className="relative">
              <select
                id="reject-reason"
                value={reason}
                onChange={(event) =>
                  setReason(event.target.value as Schemas["ReasonCode"] | "")
                }
                disabled={isPending}
                aria-required="true"
                className={cn(
                  "border-border bg-background w-full appearance-none rounded-md border",
                  "px-3 py-2 pr-8 text-[12px]",
                  "focus:ring-ring focus:ring-1 focus:outline-none disabled:opacity-50",
                )}
              >
                <option value="" disabled>
                  Choose a reason…
                </option>
                {REASON_CODES.map((code) => (
                  <option key={code} value={code}>
                    {REASON_LABELS[code]}
                  </option>
                ))}
              </select>
              <ChevronDown
                size={12}
                strokeWidth={2}
                aria-hidden
                className="text-muted-foreground pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2"
              />
            </div>
          </div>

          <div>
            <Label htmlFor="reject-notes" className="mb-1.5 flex justify-between text-[11px]">
              <span className="font-medium">Note</span>
              <span className="text-muted-foreground font-normal">Optional</span>
            </Label>
            <Textarea
              id="reject-notes"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              disabled={isPending}
              rows={3}
              placeholder="Add context for your team…"
              className="resize-none text-[12px]"
            />
          </div>

          {error != null && (
            <p
              role="alert"
              className="bg-status-rejected text-status-rejected-fg rounded-md px-3 py-2 text-[11px]"
            >
              {describeError(error)}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={isPending}
            onClick={() => onOpenChange(false)}
            className="text-[12px]"
          >
            Cancel
          </Button>
          <Button
            type="button"
            // required means required: no reason, no submit
            disabled={!reason || isPending}
            onClick={() => onConfirm({ decision: "reject", reason_code: reason || null, notes: notes.trim() || null })}
            className="bg-status-rejected-fg text-[12px] text-white hover:opacity-90"
          >
            {isPending ? "Rejecting…" : "Confirm rejection"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return "This opportunity has already been decided. Reload to see its current status.";
    }
    return typeof error.detail === "string"
      ? error.detail
      : `Rejection failed (HTTP ${error.status}).`;
  }
  return "Couldn't reach the server. Your input has been kept — try again.";
}
