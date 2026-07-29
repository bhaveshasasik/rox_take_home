# Rox Opportunity Pipeline — Backend

End-to-end **Opportunity → Qualified** pipeline over the Rox API: periodic
account research, scored opportunities, human accept/reject, automated
prospecting into real Rox sequences, and in-app reporting.

## Quick start

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in ROX_API_TOKEN
.venv/bin/uvicorn app.main:app --reload
```

Swagger UI: http://localhost:8000/docs

```bash
.venv/bin/python -m pytest -q      # 149 tests, Rox mocked via respx
```

Trigger a cycle without waiting for the scheduler:

```bash
curl -X POST localhost:8000/admin/research/run
```

## How it works

```
 scheduler (APScheduler, every RESEARCH_INTERVAL_MINUTES)
      │
      ▼
 run_research_cycle ──► sync accounts       GET  /hierarchy/customers
      │                 resolve columns     GET  /account_research/account_research_section
      │                 trigger refresh     POST /research/clever_column/{col}/refresh_by_tab/{org}
      │                 wait for jobs       GET  /priority_jobs
      │                 bulk read           GET  /agents/customers_paginated/{col}
      │                 persist ResearchArtifacts
      ▼
 create_opportunities ─► parse cells → score → dedupe → Opportunity
      │
      ▼
 notify_opportunity ───► Slack webhook + SMTP email, with a deep link
      │
      ▼
 [human] POST /opportunities/{id}/decision   accept | reject (+ reason)
      │
      ▼ (on accept, background task)
 run_prospecting ──────► GET /people → POST /sequences (one per contact)
      │
      ▼
 /reporting/*  ────────► funnel, calibration, latency, coverage, yield
```

~6 Rox requests per cycle for 3 columns, versus ~66 with per-entity cell polling.

### Research columns

Columns are **authored in the Rox UI** and referenced by name in
[`app/services/research_columns.py`](app/services/research_columns.py). Columns
created through the API never generate cells — reproducible via
[`scripts/probe_column_generation.py`](scripts/probe_column_generation.py), and
documented in [`.claude/skills/rox-api/SKILL.md`](../.claude/skills/rox-api/SKILL.md).

| key | Rox column | role |
|---|---|---|
| `opportunity_signal` | Opportunity Signal | SCORE (0-10 → ×10) |
| `risk_signal` | Risk Signal | MODIFIER, inverted |
| `engagement_recency` | Engagement Recency | CONTEXT |
| `key_contacts` | Key Contacts | CONTEXT |

**To change the signals**, edit `COLUMN_REFS` — or set `RESEARCH_COLUMNS` in
`.env` to a comma-separated list of Rox column names. Unknown names are added as
`CONTEXT`, so trialling a new column can't perturb scores. Nothing else changes:
`research.py` fetches whatever is listed and `score_account` scores from the
declared roles.

### Scoring

`parse_cell` handles the live `"7 — <rationale>"` / `"Low - <rationale>"` format
and normalizes against each column's declared `scale` — Rox emits **0-10**, so a
raw `7` must become `70`; parsing it as 7/100 silently drops every opportunity
below threshold.

`score_account` is the policy seam and is expected to change: `SCORE` columns set
the base, `MODIFIER` columns adjust it within bounds, `CONTEXT` columns never
affect it. Its tests assert *properties* (ordering, direction), not exact numbers.

### Dedupe

Research is recurring, so the same signal resurfaces every run. An opportunity is
skipped if the account already has an undecided one, or if one was decided within
`OPPORTUNITY_COOLDOWN_DAYS`. Deduping is per **account**, not per signal type —
`signal_type` is derived from rationale text and can drift between runs for the
same underlying signal.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | status, scheduler, next run, channel config |
| GET | `/accounts`, POST `/accounts/sync` | target accounts |
| GET | `/opportunities` | list + filter (status, stage, signal, score, search, sort, paging) |
| GET | `/opportunities/{id}` | detail incl. supporting research + decision |
| POST | `/opportunities/{id}/decision` | accept / reject; accept triggers prospecting |
| POST | `/opportunities/{id}/notify` | re-send notification |
| GET | `/prospecting/sequences` | all sequences |
| GET | `/prospecting/opportunities/{id}` | contacts, enrollments, generated emails |
| POST | `/prospecting/opportunities/{id}/run` | retry prospecting |
| GET | `/reporting/overview` | every dashboard section in one call |
| GET | `/reporting/{funnel,score-calibration,signal-performance,decision-latency,rejection-reasons,account-coverage,prospecting-yield,run-health,job-telemetry}` | individual reports |
| GET | `/opportunities/stats` | pending + aging counts for the pipeline header |
| GET | `/research/runs`, `/research/columns` | automation visibility |
| GET | `/notifications`, POST `/notifications/{id}/retry` | delivery audit + retry |
| POST | `/admin/research/run` | trigger a cycle now |
| GET | `/admin/rox/me` | Rox connectivity check |

Every database-backed report accepts an optional `start`/`end` window, half-open
as `[start, end)` so adjacent windows tile without double-counting a boundary
row. Each filters on the timestamp of the event it measures — opportunity
`created_at` for cohort metrics, `decided_at` for decision metrics,
`started_at` for run health; see the `app.services.reporting` docstring.
`job-telemetry` is the exception: it reads the live Rox task queue and keeps its
own `lookback_hours`. Two reports take an extra knob:

| Endpoint | Param | Effect |
|---|---|---|
| `/reporting/score-calibration` | `buckets=N` | equal-width score buckets (`10` for deciles). Omit for the default named bands `0-59 / 60-74 / 75-89 / 90-100`. |
| `/reporting/rejection-reasons` | `group_by=day\|week` | fills `series` with long-form `(period, reason_code, count)` rows for a reasons-over-time chart. Omit for totals only. |

## Reporting — and why these metrics

| Report | Business question |
|---|---|
| Funnel | Where do opportunities die? Stage attainment derives from facts (was it notified? does it have contacts?) not the mutable `stage` column, so rejections are counted at the stages they *did* reach and conversion isn't flattered. |
| **Score calibration** | Is the score predictive? Acceptance rate per score band — a flat line means the score is decoration. |
| Signal performance | Which signals produce opportunities people accept? |
| Decision latency | Is human review the bottleneck? (median / p90). Timed from `notified_at` where available, falling back to `created_at` for opportunities decided without ever being notified — otherwise the metric silently reports null whenever notifications are misconfigured, which reads as "no decisions" rather than "not measurable". `measured` vs `count` tells the two apart, and `from_notification` / `from_creation` say which basis produced the number. |
| Rejection reasons | What to fix upstream — e.g. lots of `already_engaged` ⇒ dedupe against CRM first. |
| Account coverage | Which target accounts are we ignoring entirely? |
| Prospecting yield | Does an accept reliably become real outreach? |
| Run health / job telemetry | Is the automation healthy? `cells_unscoreable` counts research that was fetched and then discarded — a rising share means Rox is returning a shape the parser no longer reads, which is otherwise invisible. `job-telemetry` reads Rox's own task queue live. |

## Configuration

See [`.env.example`](.env.example). Notable knobs:

- `RESEARCH_INTERVAL_MINUTES` — scheduler cadence
- `RESEARCH_REFRESH_ENABLED` — trigger regeneration before reading; set `false` for a fast read-only run
- `RESEARCH_COLUMNS` — override the column registry by name
- `ROX_ORG_ID` — the `refresh_by_tab` tab id; auto-discovered from `/priorities` when blank
- `ROX_WRITES_ENABLED` — create real DRAFT sequences in Rox on accept
- `OPPORTUNITY_SCORE_THRESHOLD` / `OPPORTUNITY_COOLDOWN_DAYS`
- `SLACK_WEBHOOK_URL`, `SMTP_*` — channels activate only when configured

## Known gaps

1. **Opportunity titles and outreach copy are assembled by string manipulation**
   (`clean_headline`, `_lede`, `_supporting_evidence` in
   [`prospecting.py`](app/services/prospecting.py)). This is the weakest part of
   the codebase and the natural place for an LLM to write the headline and email
   body from the research instead.
2. **No auth** on our own API; single-user demo (assignee from `/user/me`).
3. **Emails are drafted into Rox as DRAFT sequences, not sent.** Rox does not
   author the body — `campaign_request_public_id` is silently dropped on write
   and there is no campaign generate trigger, so the copy is ours.
4. **`ResearchArtifact` rows accumulate per run.** Fine at 21 accounts × 3
   columns; would need pruning at scale.
