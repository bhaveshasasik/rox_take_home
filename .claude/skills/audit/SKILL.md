---
name: audit
description: Audit a codebase end-to-end for dead code, incomplete wiring, broken/partial implementations, and database inconsistencies (schema drift, bad upserts, model creation gaps). Use whenever the user asks to "audit," "sanity check," or "review" a codebase for completeness, wiring, dead code, or database health — even if they only mention one of these, run the full checklist. Report findings before making any changes.
---

# Codebase Audit

Trace actual call paths — don't skim. Report findings first; only apply trivial, unambiguous fixes without asking.

## 1. Dead Code
- Functions/classes/modules defined but never imported or called (exclude public API entry points, tests)
- Exported symbols never consumed elsewhere in the repo
- Stale commented-out code, permanently-set feature flags with the dead branch still present
- Unused imports, variables, parameters
- Unreachable code (after unconditional return/throw/continue/break)

## 2. Wiring Completeness
- Every route has a reachable handler (right method, path, middleware, not shadowed)
- Every DB model has a matching migration (no model/schema drift)
- Every UI component is actually rendered somewhere, not just defined
- Every config/env var used in code exists in the env template, and vice versa
- Every emitted event (pub/sub, webhook, queue) has a subscriber, and vice versa
- Every DI binding/provider is actually resolved somewhere

## 3. Partial / Broken Implementations
- "Not implemented" stubs, null placeholders, TODOs standing in for real logic
- Silent exception swallowing (empty catches, log-only where caller expects a real error)
- Duplicated business logic that has diverged between copies
- Frontend/backend contract mismatches for the same resource (field names, types, enum values)

## 4. Database

**Migration/model drift** — every model field has a matching migration column and vice versa; migration chain has no gaps, duplicate revisions, or missing rollback paths.

**Schema consistency** — API request/response schemas match the underlying model (field names, types, required/nullable); JSON/JSONB columns have real validation on the write path; enum values match DB-level constraints.

**Upserts** — the conflict/uniqueness key used actually matches a real unique constraint or index (mismatched keys silently duplicate or misupdate rows); the update-on-conflict clause doesn't blindly overwrite fields that should be preserved (`created_at`, `created_by`, counters); no manual "check-then-insert" pattern where an atomic upsert should be used (race condition risk); partial updates don't null out unincluded fields.

**Model creation paths** — trace request → validation → instantiation → write → response for every creatable entity; confirm defaults match between app code and DB; confirm required FKs are validated before the parent is created; confirm auto-generated fields (IDs, timestamps, slugs) are generated exactly once, not by two disagreeing code paths; multi-table creates are wrapped in one transaction with correct rollback on partial failure.

**General hygiene** — unused tables/columns; orphaned or unindexed foreign keys; raw SQL referencing columns that no longer exist; duplicate/redundant indexes; read-modify-write paths missing row locking; seed/fixture data that no longer matches the schema.

## Output Format

```
### Summary
1 paragraph: overall health + top 3-5 issues.

### Findings
- Category: [Dead Code / Wiring / Partial Implementation / DB-Migration /
  DB-Schema / DB-Upsert / DB-ModelCreation / DB-General]
- Location: file:line
- Description: 1-2 sentences
- Evidence: the concrete proof (e.g. "upsert conflict key is `email`, unique
  constraint is on `(org_id, email)`")
- Severity: Critical / Moderate / Minor
- Suggested fix: 1 sentence

### Confirmed Working
List the major paths traced end-to-end (e.g. "Create Deal: API schema →
validation → model → migration-backed columns, all fields accounted for").
```

## Rules
- Never flag something as dead/unused without checking dynamic imports, string-based lookups, reflection, and re-exports — false positives are worse than misses.
- Don't modify code beyond trivial fixes without confirming first.
- For DB findings, always cite both sides of a mismatch, not just the side that's "wrong."
- On large codebases, prioritize core business logic and high-traffic create/upsert paths before peripheral utilities.