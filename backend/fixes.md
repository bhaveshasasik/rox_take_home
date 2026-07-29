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

## Prompt 7 — Derank `other` in the research summary

```
In the opportunity detail view's Research summary, `other` signals currently
fill the default three-row preview. For Cisco the whole preview is a
headquarters address, a website URL and an industry description, and every real
finding sits behind the expand toggle.

- Rank `other` last, below positives and absences. Do not filter it out — the
  section still lists every finding once expanded.
- The sidebar Signals rail already drops `other` entirely. Leave it as is; the
  two panels are answering different questions.
- Ranking is display-only. Do not change `_signal_rows`' SQL ordering, which
  scoring and the brief also read.

Report the top three signals for Cisco, Ford and Wells Fargo before and after.
```

`other` scores zero by definition, so the preview currently spends all three of
its rows on findings that cannot affect the outcome. This is the cheapest fix
in the list and needs no re-extraction.

**Acceptance:** no score moves. No signal disappears from the expanded list.

---

## Prompt 8 — Group absences that share one evidence span

```
One "nothing was found" sentence in the research becomes one absence signal per
category, all carrying the same verbatim span — that is correct and must not
change in extraction. But the Research summary renders only the evidence, so
Cisco prints six rows of byte-identical text, Ford five, Walmart four.

In the frontend only:
- Group absence signals by their evidence span
- Render one block per distinct span, listing every category it covers
- A single-category absence renders exactly as it does today
- Positives and contested signals are never grouped — only absences

This is display grouping, not extraction dedupe. The stored signals, their
count, and the score composition are unchanged.

Report the rendered row count per account before and after.
```

The "Don't touch — dedupe" rule above means extraction dedupe. Collapsing
identical spans at render time is the opposite: it keeps every typed signal and
stops showing the same sentence six times.

**Acceptance:** score totals identical. `extracted_signals` length unchanged in
the API response — only the rendered row count drops.

---

## Prompt 9 — Remove rationale from extraction

```
Drop the per-signal rationale. Extraction returns the signal type, confidence,
is_absence and the verbatim evidence span — nothing generated.

Backend, in one commit:
- Remove `rationale` from SignalDraft, ExtractedSignal and ExtractedSignalOut
- SYSTEM_PROMPT: delete the "rationale is your own one-sentence explanation"
  rule, and delete the "Do not encode the absence only in your rationale" rule.
  The second only exists because the field does — with no rationale to write,
  the model cannot state an absence in prose while setting is_absence=false, so
  the guard becomes structural instead of instructional. Change nothing else in
  the prompt: the vocabulary, the is_absence mechanical test, the nested-shape
  rule and the shared-span absence rule are all load-bearing and unrelated.
- Remove `rationale` from the ExtractedSignalRow write path and the twin-reuse
  copy in extract_artifact
- elaboration.format_signals: drop the "Why it matters" line. The brief then
  reasons from label, confidence, absence/contested markers and the verbatim
  span only
- _artifact_out: `narrative` currently joins signal rationales. Set it to None
  on the extracted path rather than substituting evidence — it is only read by
  the pre-extraction renderer
- Keep AccountBriefOut.rationale. That is the brief's prose, not a signal's
- Leave the ExtractedSignalRow.rationale column in place. Old rows keep it and
  the version filter already hides them
- Bump SCHEMA_VERSION to 2, re-extract all artifacts, regenerate the frontend
  types

ABSENCE VERIFICATION — replace contests_absence, do not extend it

`contests_absence` under-detects, and the miss is already scoring. Three Abbott
signals quote "Because those items are absent from the internal dataset, no
confidence-rated buying-intent, champion..." with is_absence=0 AND contested=0,
at confidence 8 on three scoring categories. Abbott scores 95.

Two independent failures stacked:
- `_ABSENCE_ASSERTION` has no pattern for "absent" in any form
- its `no <up to 3 words> <noun>` branch should have matched "no
  confidence-rated buying-intent, champion" — `champions?` is in the noun list
  — but `[a-z][a-z-]*\s+` cannot consume "buying-intent," because of the comma

Replace the regex with a cheap model call over the evidence span: does this
span assert that something was NOT found? Binary, low effort, no reasoning.

- Run it only where is_absence=false and signal_type != other. Everything else
  either already reports an absence or cannot score.
- On disagreement set contested=true, exactly as today. Do not auto-flip
  is_absence — withholding from the score is the protection needed, and
  rewriting the model's own output on an automated second opinion is a larger
  change than this checklist should make.
- Delete `_ABSENT_NOUNS` and `_ABSENCE_ASSERTION` with it. Keeping a
  hand-maintained noun list beside a model check gives two sources of truth
  and no way to tell which one failed.

Report the contested rate before and after. It was 41/622 = 6.6% under the
regex, which is a floor, not a measurement — the Abbott rows show it misses.
```

Rationale was the last generated field on a signal, and the only one that could
not be checked against the source. Removing it shortens the prompt, removes a
hallucination surface outright rather than mitigating it, and makes the brief
work from verbatim spans alone. Principle 2, applied to the schema rather than
just the display. The frontend already ignores the field, so nothing there
changes.

**Acceptance:** evidence validation failure rate stays at 0. Signal counts per
account do not drop materially — a large fall means the shortened prompt lost
coverage, not that the cells changed.

**Regression target:** Abbott's three signals over the "absent from the
internal dataset" span must come back either `is_absence=true` or
`contested=true`. If they return unflagged again, the absence verification did
not land and nothing else in this prompt matters. This is the specific case to
check first, before reading the score diff.

**Score movement is expected here, unlike everywhere else in this document.**
A schema bump forces re-extraction, and a re-run can legitimately return a
different signal set. Diff the scores, review the movement account by account,
and accept it deliberately. Do not treat a moved score as an automatic revert —
and do not skip the diff because movement is expected.

---

## Known bad row — Abbott

Abbott is **accepted** at a score of 95, built on three signals whose own
evidence says the findings are absent. `needs_review` was already 1; it was
accepted anyway, which is worth knowing on its own — the flag did not change
the outcome.

Nothing in this checklist corrects it, deliberately. Every script here skips
decided opportunities because rewriting the evidence behind a decision
falsifies the record, and that reasoning does not weaken just because the
decision looks wrong in hindsight. Prompt 9 will re-extract the artifact and
fix the *signals*; the opportunity keeps its score and its acceptance.

Correcting the decision is a product call, not a migration:

- leave it, and let the re-extracted signals disagree with the stored score
- re-open it for review, which means reversing an accept a human made
- record a correction alongside it rather than overwriting either

Pick one deliberately. Do not let a backfill make the choice silently by
running with `--include-decided`.

---

## Don't touch

Extraction signal coverage · score composition weights · dedupe · the
`SignalType` enum · anything in the reporting layer.

## Watch for

Any commit where a score total changes. Nothing in this checklist should move a
score — every item is display or generation quality. A moved score means a
display concern leaked into scoring, and it should be reverted rather than
re-baselined.