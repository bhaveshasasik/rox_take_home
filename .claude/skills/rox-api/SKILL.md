---
name: rox-api
description: Conventions, verified request/response shapes, and gotchas for the Rox API at core.roxai.dev. Use whenever calling or debugging Rox endpoints — accounts (/hierarchy/customers), research sections and clever_column cells, people/contacts (/people), or sequences (/sequences) — or when deciding which Rox endpoint can supply a given piece of data.
---

# Rox API

Base URL `https://core.roxai.dev/api/v1`, auth `Authorization: Bearer $ROX_API_TOKEN`.
Everything below was verified live on 2026-07-27 against org `Rox SE
<roxdemointerview4@workday.com>` (21 accounts). Client lives in
`backend/app/rox/client.py`.

## Conventions

- **No response envelope on most endpoints.** `/hierarchy/customers` and
  `/account_research/account_research_section` return **bare JSON arrays**.
  `/people` is the exception and returns a paginated object.
- **Errors** are `{"messages": [...]}` (FastAPI-style, 422) or
  `{"errors": {field: msg}, "message": ...}` (flask-restx-style, 400).
- **Empty-body POSTs are the fastest way to learn a schema.** Both error styles
  enumerate the missing required fields, so `POST` with `{}` and read the reply.
  Nest one level at a time to unwrap sub-schemas.
- Dates are `yyyy-mm-dd`. Timestamps are ISO-8601, sometimes `Z`-suffixed.

## Endpoints

### `GET /user/me`
```json
{"name": "Rox SE", "email": "roxdemointerview4@workday.com"}
```
No id field. Use `email` as the assignee identity.

### `GET /hierarchy/customers`
Bare array. Accounts are `customer_id`/`customer_name` (**not** `id`/`name`),
nested under `children`, with `hierarchy_parent_id`.
```json
[{"customer_name": "Tesla", "customer_id": "ad901858-...", "domain": "tesla.com",
  "hierarchy_parent_id": null, "children": [], "agent_status": "AVAILABLE",
  "events": [], "priorities": [], "sources": [{"source": "ROX", "source_system": "ROX"}]}]
```

### `GET /account_research/account_research_section`
Bare array of ~95 sections. **`id` is the section; `column_id` is the column** —
cells are addressed by `column_id`, not `id`.
```json
[{"id": "...", "name": "Opportunity Signal", "description": "created from a workflow",
  "column_id": "ece93caa-...", "created_by_name": "Workday_Interview", "order": null}]
```
Sections repeat by name — dedupe on `column_id`.

### `GET /research/clever_column/{column_id}/cell/{entity_id}`
`entity_id` is the `customer_id`. **This is the main research source.**
```json
{"column_id": "...", "entity_id": "...", "rox_company_id": "...",
 "cell_value": "7 — Strongest signals are recent executive moves...",
 "output": {"type": "text", "text": "7 — Strongest signals...", "sources": []},
 "updated_at": "2026-07-27T22:58:12Z"}
```

- ⚠️ **`output` is a dict and therefore truthy.** A generic
  "first non-empty key" extractor will treat an ungenerated cell as populated
  and return a dict repr. Read `cell_value`, then fall back to `output.text`.
- There is **no status field**. Ungenerated == `cell_value: ""`. Emptiness is
  the only readiness signal.
- ⚠️ **~30% of columns return 403** `"Section associated with this column is not
  available for the user"`. Probe accessibility per column; never assume.
- `output.sources` carries citations when present (often `[]`).

### `POST /research/clever_column`
Works, but see the generation gotcha below.
```json
{"org_wide": false, "hidden": false,
 "column_config": {
   "name": "My Column",
   "column_value_format": "TEXT",           // BOOLEAN | ENUM | NUMBER | TEXT | DATE
   "enum_values": [],                        // optional
   "workflow": {"steps": [{
       "name": "research",
       "instructions": "<the prompt>",       // NOT "prompt"
       "tool_settings": {},                  // permissive; unknown keys ignored
       "model_type": "fast"                  // fast | standard | deep
   }]}}}
```
Returns `{"success": true, "column_id": "..."}` — **no section id**.

`column_config` accepts only `name`, `column_value_format`, `workflow`,
`enum_values`. There is **no way to scope a column to specific entities**.

### `POST /research/clever_column/auto_config`
`{"user_prompt": "<natural language>"}` → a complete generated `column_config`
plus a `reasoning` string explaining the choices. **Does not create anything.**

```json
{"reasoning": "The user wants ... a TEXT column is best because ...",
 "column_config": {"name": "Buying Signals (6M) Score", "column_value_format": "TEXT",
                   "enum_values": [], "workflow": {"version": 1, "steps": [
                       {"name": "Find Recent Buying Signals and Score Opportunity",
                        "instructions": "<long generated prompt>", ...}]}}}
```
Genuinely useful: it authors a far better `instructions` prompt than hand-writing
one, picks `model_type` (`deep` for synthesis tasks), and returns `version: 1` on
the workflow — a field the manual endpoint does not document. Feed its output
straight into `POST /research/clever_column`.

### `POST /research/clever_column/auto_create`
`{"user_prompt": "...", "org_wide": true}` → `{"success": true, "column_id": "..."}`.
Documented as "create a placeholder column and generate config + cells in the
background."

### `GET|POST /agents/customers_paginated/{column_id}` ⭐ — bulk cell read

**Use this instead of per-entity cell calls.** One request returns every customer's
value for a column, so 3 columns × 21 accounts drops from **63 requests to 3**.

```json
[{"customer_id": "...", "domain": "tesla.com",
  "Opportunity Signal": "7 — Strongest signals are...",   // keyed by column NAME
  "section_id": "...", "column_name": "Opportunity Signal",
  "value_structured": {"string_value": "7 — Strongest signals are..."}}]
```

Read `value_structured.string_value` — it's stable, whereas the sibling key is
named after the column and changes per column.

- Values are **byte-identical** to `GET .../cell/{entity_id}` — no truncation
  penalty for going bulk. (Stored values are themselves capped ~300 chars with a
  trailing `...`; that is Rox's own truncation, present in both paths.)
- ⚠️ **POST does NOT generate cells — it is the same read as GET.** Verified
  against an ungenerated column: returned 21 rows of `null`, enqueued **zero**
  priority jobs, still 0/21 after 2 minutes. The only generation trigger is
  `refresh_by_tab` on a UI-created column.
- An ungenerated cell is `"value_structured": null` (and the column-named key is
  `null` too) — a cleaner emptiness check than the per-entity endpoint, where
  `output` is a truthy dict wrapping an empty string.
- ⚠️ **"paginated" is a misnomer at this size** — `page`, `page_size`, `limit`,
  `offset`, `per_page` are all ignored and every call returns all 21 accounts.
  Do not rely on paging without re-verifying at larger scale.
- ⚠️ **Row order is non-deterministic** between calls. Key by `customer_id`.
- Returns `domain` but **not** the customer name — join to
  `/hierarchy/customers` for names.
- Works for sparsely-populated columns (`Key Contacts` → 10/21).

### `POST /research/clever_column/{column_id}/refresh_by_tab/{tab_id}` ⭐

**This is the recurring-regeneration trigger.** Empty body. Returns
`{"message": "Successfully triggered refresh for column {col} for tab {tab}"}`.

Passing the **`rox_org_id`** as `tab_id` refreshes **all** accounts. Verified on
`Opportunity Signal`: within 20s it enqueued `1 × CUSTOM_COLUMN_GENERATION` +
`21 × CUSTOM_CELL_GENERATION`, all `COMPLETED` in **~175s**, with genuinely new
content (Genpact `7 → 2`, HP `4 → 8`).

⚠️ **The success message is not validation.** It returns the same 200 for any
UUID — including all-zeros — and for columns where nothing happens. Confirm real
work via `/priority_jobs`, never by the response body.

### 🚨 Columns created via the API never generate — but UI-created ones refresh fine

The critical distinction, verified in one org within the same hour:

| Column origin | Trigger | Jobs enqueued | Cells |
|---|---|---|---|
| **UI** (`Opportunity Signal`) | `refresh_by_tab` | `CUSTOM_COLUMN_GENERATION` + 21 × `CUSTOM_CELL_GENERATION` | **21/21 refreshed** |
| **API** (`POST`, `auto_create`) | *(none at creation)* | **none** | 0/21 |
| **API** | `PATCH`/`PUT .../{column_id}` ×12 | `PROPAGATE_CUSTOM_COLUMN_UPDATE`, **0 children** | 0/21 |
| **API** | `refresh_by_tab` | `PROPAGATE_CUSTOM_COLUMN_UPDATE`, **0 children** | 0/21 |

`PROPAGATE_CUSTOM_COLUMN_UPDATE` propagates a config change; it never fans out
to cell generation. No hidden flag rescues an API-created column — `regenerate`,
`force_regenerate`, `generate_cells`, `backfill`, `recompute`, `entity_ids`,
`rox_company_ids` are all silently **dropped** (the endpoint ignores unknown
fields, so "accepted" means nothing).

**Therefore: author columns in the Rox UI, then drive regeneration from the API
via `refresh_by_tab`.** Do not build a pipeline on columns created via the API.

### `POST /research/clever_column/feedback`
Requires `column_id`, `rox_company_id`, `rox_org_id`, `rox_user_id`, `thumbs_up`.
Note `/user/me` returns **no ids**, so `rox_user_id`/`rox_org_id` must come from
elsewhere — `POST /sequences` returns both in its response body.

### `GET /priority_jobs`, `GET /priority_jobs/{task_run_id}`
Read-only (`Allow: GET, HEAD, OPTIONS` — you cannot POST a job). The async task
queue behind research. **Use it to verify that a trigger actually did work.**

```json
{"id": 3417130356, "run_id": "...", "parent_task_run_id": "...", "root_task_run_id": "...",
 "task_type": "CUSTOM_CELL_GENERATION", "artifact_id": "<customer_id>",
 "current_state": "COMPLETED", "task_priority": "P2",
 "created_on": "...", "last_modified": "...",
 "rox_org_id": "...", "rox_user_id": "...", "aggregate_status_of_children": "COMPLETED"}
```

Task types seen: `CUSTOM_COLUMN_GENERATION` (parent, `artifact_id` = column_id),
`CUSTOM_CELL_GENERATION` (child, **`artifact_id` = customer_id**),
`PROPAGATE_CUSTOM_COLUMN_UPDATE` (config propagation only).
States: `COMPLETED`, `RUNNING`; docs also mention `SKIPPED`.

Query params: `task_type`, `task_state`, `rox_company_id`, `num_lookback_days`,
`num_lookback_hours`. Duration = `last_modified - created_on` (~90s per cell).

This is also the best source of automation-health telemetry for reporting.

### `POST /unified_data/column_state`, `POST /unified_data/column_metadata`
CRM field provenance, not research. `column_state` takes
`{object_logical_name, field_logical_names[], rox_entity_ids[]}` and returns
`{"states": {...}, "provider_ids": {...}, "last_updated": {...}}`;
`column_metadata` takes `{logical_names: [{object_logical_name, field_logical_name}]}`.
Valid objects: `company, person, deal, user, organization, event, lead,
rox_email, rox_email_event, sequence, sequence_task, contract*`.
Returned **empty** in this org (no CRM connected) — not useful here.

### `GET /people?rox_company_id={customer_id}`
Contact discovery for prospecting. Paginated object, not a bare array.
```json
{"data": [{"rox_person_id": "b2e326df-...", "rox_company_id": "...", "person_id": "...",
           "name": "Keiaaron Majette", "title": "Human Resources Manager",
           "email": null, "linkedin_url": "https://www.linkedin.com/in/...",
           "seniority": null, "persona": "ANY", "engagement_status": "new",
           "company_domain": "tesla.com", "city": "Raleigh-Durham-Chapel Hill Area"}],
 "next_page_id": null, "total_count": 6, "total_pages": 1,
 "extra_info": {"engagement_status_counts": {}}, "job_titles": null}
```
Requires `rox_company_id` (422 without it). In this org: 53 people over 11 of 21
accounts; 41 have `email`, all 53 have `linkedin_url`. `seniority` is often
`null`, `persona` is usually `"ANY"` — **filter on `title` text**, not those.

### `POST /sequences`
Creates a real sequence in `DRAFT`.
```json
{"background_content": {},                    // object, NOT a string
 "customer_id": "<customer_id>",
 "rox_person_id": "<rox_person_id>",
 "sequence_tasks": [{"background": {}, "content": {},
                     "scheduled_send_date": "2026-08-01"}]}
```
Returns the full sequence incl. `public_id`, `status: "DRAFT"`, `rox_org_id`,
`rox_user_id`, and `sequence_tasks[].public_id`.

⚠️ `rox_person_id` is **not FK-validated** — an all-zeros UUID is accepted. Pass
a real id from `/people` or you will silently create orphaned sequences.
One sequence targets one person, so enrolling N contacts = N calls.

`GET /sequences` requires a `campaign_id` query param (422 without it).

## Parsing populated research cells

The three columns populated across all 21 accounts do **not** use a labelled
format. They lead with a value, then a dash, then prose:

| Column | Coverage | Format | Notes |
|---|---|---|---|
| `Opportunity Signal` | 21/21 | `7 — <rationale>` | **score is 0-10**, em-dash `—` (U+2014) |
| `Risk Signal` | 21/21 | `Low - <rationale>` *or* `2 - <rationale>` | mixed word/number; **inverted** (low = good) |
| `Engagement Recency` | 21/21 | `N/A - <rationale>` | `N/A` is common |
| `Board Priorities` | 2/21 | prose | sparse |
| `Firmographics` | 2/21 | prose | sparse |

Gotchas that will silently corrupt scoring:
- **Scale is 0-10, not 0-100.** A naive "first integer" parse yields `7`, which
  fails a 0-100 threshold of 60. Multiply by 10.
- **Separators differ**: Opportunity Signal uses an em-dash `—`, Risk Signal a
  hyphen `-`. Match both.
- Risk Signal mixes `Low|Medium|High` with `0-10`. Handle both, and remember a
  *low* risk value is a *good* sign — do not add it to a score as-is.
- Long values appear truncated with a trailing `...` in the stored text.

## Discovery script

`backend/scripts/explore_rox.py` dumps live shapes to
`backend/artifacts/rox_shapes/`. Run it before trusting any extractor:

```bash
cd backend && .venv/bin/python -m scripts.explore_rox
```

## Housekeeping

There is no documented DELETE for clever_columns. Probe columns created during
discovery (`ZZ Probe Column`, `ZZ TS Probe`, `ZZ OrgWide Probe`) remain in the
org and will show up in `/account_research/account_research_section`. Filter
names prefixed `ZZ ` when listing sections.
