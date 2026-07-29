# Rox Opportunity Pipeline — Backend

End-to-end **Opportunity → Qualified** pipeline over the Rox API: daily
account research, LLM-extracted signals, scored opportunities, a single
review digest, human accept/reject, automated prospecting into real Rox
sequences, and in-app reporting.

## Quick start

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in ROX_API_TOKEN
.venv/bin/uvicorn app.main:app --reload
```

Swagger UI: http://localhost:8000/docs

```bash
.venv/bin/python -m pytest -q      # 253 tests, Rox and Anthropic mocked
```

Trigger a cycle without waiting for the daily schedule — the demo path:

```bash
curl -X POST "localhost:8000/admin/research/run?force_extract=true&ignore_cooldown=true"
```

## How it works

```
 scheduler (APScheduler, cron — daily at RESEARCH_RUN_AT, UTC)
      │     startup reaper marks orphaned "running" runs failed;
      │     a lost same-day scheduled run triggers a catch-up
      ▼
 run_research_cycle ──► sync accounts       GET  /hierarchy/customers
      │                 resolve column      GET  /account_research/account_research_section
      │                 trigger refresh     POST /research/clever_column/{col}/refresh_by_tab/{org}
      │                 wait for jobs       GET  /priority_jobs
      │                 per-account read    GET  /research/clever_column/{col}/cell/{entity}
      │                 persist ResearchArtifacts (full output.text narrative)
      ▼
 signal extraction ────► one LLM pass per cell (app/signals): typed signals,
      │                  verbatim evidence, whitelisted sources, 0-100 score
      ▼
 create_opportunities ─► score → dedupe/supersede → Opportunity
      │
      ▼
 send_digest ──────────► ONE email per producing run (SMTP): account, signal
      │                  label, score, review link — structured values only,
      │                  membership recorded for dedupe
      ▼
 [human] POST /opportunities/{id}/decision   accept | reject (+ reason)
      │
      ▼ (on accept, background task)
 run_prospecting ──────► GET /people → POST /sequences (one per contact)
      │
      ▼
 /reporting/*  ────────► funnel, calibration, latency, coverage, yield
```

Reads are per-account rather than bulk because only the per-entity endpoint
returns `output.text` — the full research narrative; the bulk endpoint caps
cells at ~300 characters and truncates mid-sentence.

### Research column

The pipeline reads one Rox column, **authored in the Rox UI** and referenced
by name in
[`app/services/research_columns.py`](app/services/research_columns.py) —
columns created through the API never generate cells (reproducible via
[`scripts/probe_column_generation.py`](scripts/probe_column_generation.py)).

| key | Rox column | role |
|---|---|---|
| `opportunity_signal` | Opportunity-Signal Research | the scored research narrative |

To trial other columns, set `RESEARCH_COLUMNS` in `.env` to a comma-separated
list of Rox column names; unknown names are fetched but never perturb scores.

### Scoring

The primary path is structured extraction (`app/signals`): one LLM pass per
research cell returns typed signals — a fixed seven-value vocabulary,
confidence 0-10, an explicit absence flag, and a verbatim evidence span that
is validated against the source text. Scoring composes those into a 0-100
total with a stored factor breakdown; absences, contested signals, and
out-of-category context score zero by construction. Extraction runs inside
the scheduled cycle (`EXTRACTION_ENABLED`, on by default — one run per day is
~21 LLM calls plus a ~33% retry overhead).

The regex parser (`app/services/parsing.py`) survives only as the fallback
for artifacts that have never been extracted.

### Dedupe and superseding

Research recurs, so the same signal resurfaces every run. An account is
skipped if it was decided within `OPPORTUNITY_COOLDOWN_DAYS`, or if it holds
an undecided opportunity — unless the new score clears the open one by 15
points, in which case the open opportunity is closed as **superseded** and
the stronger one takes its place. Superseded is terminal but carries no
decision: it never appears in acceptance rates, rejection reasons, or the
review queue. Deduping is per **account**, not per signal type.

Future work, deliberately not built: expiring stale unreviewed opportunities
on a timer. Superseding covers the case that actually deadlocks the pipeline
(a low score blocking its account forever); general expiry is policy the
demo doesn't need.

### Notifications

One **digest per producing run**, email only — there is no Slack integration.
Each entry is structured values end to end: account name, primary signal
label (the scoring driver), score, review link, plus a link to the pipeline
pre-filtered to the digest's own view. No LLM-generated prose can reach the
email.

Two thresholds, kept separate on purpose:

- `OPPORTUNITY_SCORE_THRESHOLD` (60) gates **creation** — what enters the
  pipeline at all.
- `NOTIFICATION_SCORE_THRESHOLD` (80) gates **digest inclusion** — what is
  worth a reviewer's inbox. Sub-80 opportunities exist in the pipeline UI,
  are never emailed, and can still supersede or be superseded.

Every digest records its membership (`digests` / `digest_opportunities`), and
eligibility is one shared query: at/above the notification floor, undecided,
and not a member of any prior digest that was or may still be delivered.
Running the digest twice sends nothing the second time.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | status, scheduler, next run, channel config |
| GET | `/accounts`, POST `/accounts/sync` | target accounts |
| GET | `/opportunities` | list + filter (status, stage, signal, score, search, sort, paging) |
| GET | `/opportunities/{id}` | detail incl. research, extracted signals, score breakdown |
| POST | `/opportunities/{id}/decision` | accept / reject; accept triggers prospecting |
| POST | `/opportunities/{id}/notify` | re-send a single notification |
| GET | `/prospecting/sequences` | all sequences |
| GET | `/prospecting/opportunities/{id}` | contacts, enrollments, generated emails |
| POST | `/prospecting/opportunities/{id}/run` | retry prospecting |
| GET | `/reporting/overview` | every dashboard section in one call |
| GET | `/reporting/{funnel,score-calibration,signal-performance,decision-latency,rejection-reasons,account-coverage,prospecting-yield,run-health,job-telemetry}` | individual reports |
| GET | `/opportunities/stats` | pending + aging counts for the pipeline header |
| GET | `/research/runs`, `/research/columns` | automation visibility |
| GET | `/notifications`, POST `/notifications/{id}/retry` | delivery audit + retry |
| POST | `/admin/research/run` | trigger a cycle now; `force_extract` / `ignore_cooldown` per-run overrides, recorded on the run row |
| POST | `/admin/notifications/digest` | send the digest now (membership-deduped) |
| POST | `/admin/signals/extract` | run extraction over stored artifacts |
| GET | `/admin/signals/health` | extraction coverage and yield tripwires |
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
| Funnel | Where do opportunities die? Stage attainment derives from facts (was it notified? does it have contacts?) not the mutable `stage` column, so rejections — and superseded rows — are counted at the stages they *did* reach and conversion isn't flattered. |
| **Score calibration** | Is the score predictive? Acceptance rate per score band — a flat line means the score is decoration. Superseded rows never count as decided. |
| Signal performance | Which signals produce opportunities people accept? |
| Decision latency | Is human review the bottleneck? (median / p90). Timed from `notified_at` where available, falling back to `created_at` for opportunities decided without ever being notified — otherwise the metric silently reports null whenever notifications are misconfigured, which reads as "no decisions" rather than "not measurable". `measured` vs `count` tells the two apart, and `from_notification` / `from_creation` say which basis produced the number. |
| Rejection reasons | What to fix upstream — e.g. lots of `already_engaged` ⇒ dedupe against CRM first. |
| Account coverage | Which target accounts are we ignoring entirely? |
| Prospecting yield | Does an accept reliably become real outreach? |
| Run health / job telemetry | Is the automation healthy? `cells_unscoreable` counts research that was fetched and then discarded — a rising share means Rox is returning a shape the parser no longer reads. Orphaned "running" rows are reaped at startup and marked failed with a reason naming the reaper; forced runs carry their overrides so their numbers don't read as trend. `job-telemetry` reads Rox's own task queue live. |

## Configuration

See [`.env.example`](.env.example). Notable knobs:

- `RESEARCH_RUN_AT` — daily run time, `HH:MM` UTC (default `07:00`). Invalid
  values fail startup rather than silently degrade. Blank falls back to…
- `RESEARCH_INTERVAL_MINUTES` — legacy interval cadence, kept for calibration
  work only
- `EXTRACTION_ENABLED` — structured extraction inside the cycle (default on)
- `OPPORTUNITY_SCORE_THRESHOLD` / `NOTIFICATION_SCORE_THRESHOLD` — creation
  vs digest-inclusion floors; see Notifications for why they differ
- `OPPORTUNITY_COOLDOWN_DAYS` — re-surfacing window after a decision
- `RESEARCH_REFRESH_ENABLED` — trigger regeneration before reading; set `false` for a fast read-only run
- `RESEARCH_COLUMNS` — override the column registry by name
- `ROX_ORG_ID` — the `refresh_by_tab` tab id; auto-discovered from `/priorities` when blank
- `ROX_WRITES_ENABLED` — create real DRAFT sequences in Rox on accept
- `SMTP_*`, `NOTIFY_EMAIL_TO` — email activates only when configured; there
  is no other channel

## Known gaps

1. **No auth** on our own API; single-user demo (assignee from `/user/me`).
2. **Emails are drafted into Rox as DRAFT sequences, not sent.** Rox does not
   author the body — `campaign_request_public_id` is silently dropped on write
   and there is no campaign generate trigger, so the copy is ours.
3. **`ResearchArtifact` rows accumulate per run.** Fine at 21 accounts;
   would need pruning at scale.
4. **`force_extract` cannot bypass the content-hash twin lookup** — an
   artifact whose text matches another's completed extraction copies rows
   instead of calling the model (~4% of cells). Force does not strictly mean
   "call the model".
