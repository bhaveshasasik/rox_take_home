# Signal display + hallucination fixes — prompt checklist

Run these in order, one per commit. Don't batch them.

## Principles

1. **Extract all signals, display three.** Scoring uses corroboration, absences,
   and contested — those need the full set. Ranking and truncation are display
   concerns and belong in the frontend or the response, never in extraction.
2. **Prefer extracted text over generated text.** Evidence is a verbatim span
   and can be validated. Rationale is generated prose and cannot. Every place
   generated text is replaced by extracted text is a hallucination surface
   removed rather than mitigated.
3. **Constrain generation at write time, not display time.** Truncating a long
   or wrong output in CSS hides the problem and keeps the bad text in the
   database.

---

## Prompt 1 — Diagnose the truncation

```
The rationale at the top of the opportunity detail view is truncated. Find out
where, before changing anything.

Check all three layers and tell me which one is responsible:
1. Stored — is the value already truncated in the database?
2. Response — does the API serializer cut it, or is there a max length on the
   Pydantic model?
3. Render — is it CSS (line-clamp, overflow, fixed height) or a JS slice in
   the component?

Report which layer, with the line that does it. Don't fix it yet.
```

Each layer has a different fix and a different blast radius. A stored
truncation means data loss that needs re-generation; a CSS clamp is a one-line
change. Fixing blind risks patching the render while the database stays wrong.

---

## Prompt 2 — Rank signals and cap the display

```
Extraction keeps extracting all signals — do not change that. Scoring depends
on the full set.

Add ranking and display capping:

- Rank signals by strength/confidence, with a deterministic tiebreak so order
  is stable across renders
- The detail view shows the top 3 by default
- If more exist, show a count and let the user expand to see the rest — don't
  silently hide them
- Score breakdown still composes from ALL signals, not the displayed three.
  Verify the total is unchanged after this commit.

Confirm before you start: is ranking better placed in the API response or the
frontend? Recommend one and say why.
```

**Acceptance:** score totals identical before and after. If any score moves,
ranking leaked into scoring.

---

## Prompt 3 — Evidence instead of rationale on signals

```
On the research signals in the opportunity detail view, show the verbatim
evidence span and drop the per-signal rationale.

- Evidence renders as the signal's supporting text
- Keep the source link
- If a signal has no evidence, say so explicitly rather than falling back to
  rationale
- Don't delete rationale from the schema or database yet — just stop
  displaying it

Report how many of the 21 opportunities have signals with missing or empty
evidence.
```

That last line matters. If evidence is frequently missing, the extraction
prompt isn't reliably capturing verbatim spans, and prompt 4 needs to fix that
before this display change is safe.

---

## Prompt 4 — Constrain generation and validate evidence

```
Two changes to the extraction and brief generation.

EVIDENCE VALIDATION
- Evidence must be a verbatim substring of the source cell text. Add a
  containment check after extraction.
- On failure, retry once. On second failure, persist the signal with evidence
  null and an explicit validation_failed flag — do not keep an unvalidated span.
- Report the failure rate across a full re-extraction of existing artifacts.

RATIONALE GENERATION
- Hard word limit on the generated opportunity rationale. Enforce it in the
  prompt AND validate the output length — regenerate once if over.
- The rationale may only make claims traceable to extracted signals and their
  evidence. It must not introduce facts, figures, company details, or dates
  that don't appear in the source.
- Lower temperature for this call.
- State the limit you chose and why.

Do not fix hallucination by truncating output. A truncated hallucination is
still wrong, and the full text stays in the database.
```

**Acceptance:** re-extraction reports an evidence validation failure rate. If
it's high, the extraction prompt needs work before anything else ships.

---

## Prompt 5 — Fix the truncation properly

Only after prompt 1's diagnosis and prompt 4's length constraints.

```
Fix the rationale truncation at the layer prompt 1 identified.

If the cause is stored data: regenerate under the new length constraint rather
than restoring the old long values.

If the cause is render or response: remove the cap, and confirm the layout
holds now that rationale length is constrained at generation. Check the longest
rationale in the data.

Either way, verify the detail view against the longest and shortest rationale
present.
```

---

## Prompt 6 — Sweep for remaining generated text

```
Report without fixing: every place the frontend displays LLM-generated prose
rather than extracted or structured data.

For each, say whether an extracted field could replace it. Include the
opportunity headline and any prospecting email copy.
```

The remaining generated text is the remaining hallucination surface. This
tells you how much is left after prompts 3 and 4.

---

## Don't touch

Extraction signal coverage · score composition weights · dedupe · the
`SignalType` enum · anything in the reporting layer.

## Watch for

Any commit where a score total changes. Nothing in this checklist should move a
score — every item is display or generation quality. A moved score means a
display concern leaked into scoring, and it should be reverted rather than
re-baselined.