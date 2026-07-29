"use client";

import { Pill, type PillTone } from "@/components/pipeline/status-pill";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatAge } from "@/lib/pipeline";

import type { Enrollment, OutreachEmail } from "./use-prospecting";

const EMAIL_TONES: Record<OutreachEmail["status"], PillTone> = {
  draft: "neutral",
  scheduled: "info",
  sent: "info",
  opened: "positive",
  replied: "positive",
};

/**
 * Full email bodies, read without leaving the page. Bodies run to ~950 chars
 * and carry their own line breaks, so they are rendered pre-wrapped rather
 * than collapsed into a paragraph.
 */
export function EmailDialog({
  enrollment,
  onClose,
}: {
  enrollment: Enrollment | null;
  onClose: () => void;
}) {
  const contact = enrollment?.contact;

  return (
    <Dialog open={enrollment !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] gap-0 overflow-hidden p-0 sm:max-w-[640px]">
        <DialogHeader className="border-border border-b px-5 py-4">
          <DialogTitle className="text-[13px] font-semibold">
            {contact?.name ?? "Outreach"}
          </DialogTitle>
          <DialogDescription className="text-muted-foreground text-[11px]">
            {/* who this is going to — the relevant question when reading a draft */}
            {[contact?.title, contact?.email].filter(Boolean).join(" · ") ||
              "No contact details recorded"}
          </DialogDescription>
        </DialogHeader>

        <div className="overflow-y-auto px-5 py-4">
          {(enrollment?.emails ?? []).length === 0 ? (
            <p className="text-muted-foreground text-[12px]">
              No emails were drafted for this contact.
            </p>
          ) : (
            <ol className="space-y-5">
              {(enrollment?.emails ?? []).map((email) => (
                <li key={email.id} className="border-border border-b pb-5 last:border-0 last:pb-0">
                  <div className="mb-2 flex items-baseline justify-between gap-3">
                    <span className="text-muted-foreground text-[10px] font-semibold tracking-widest uppercase">
                      Step {email.step_number}
                    </span>
                    <div className="flex items-center gap-2">
                      {email.sent_at && (
                        <span className="text-muted-foreground font-mono text-[10px]">
                          sent {formatAge(email.sent_at)} ago
                        </span>
                      )}
                      <Pill tone={EMAIL_TONES[email.status]}>{email.status}</Pill>
                    </div>
                  </div>

                  <p className="mb-2 text-[13px] font-medium">{email.subject}</p>
                  <p className="text-[12px] leading-[1.65] whitespace-pre-wrap">{email.body}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
