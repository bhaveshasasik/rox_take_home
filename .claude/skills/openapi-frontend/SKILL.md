---
name: openapi-frontend
description: Build React screens that are grounded in an OpenAPI spec, so components use real field names and response shapes instead of invented ones. Use this whenever the user asks to build, scaffold, wire up, or style any frontend screen, page, list, table, form, detail view, or dashboard in a repo that contains an OpenAPI or Swagger spec — including when they hand over a Figma frame, a Figma link, or a screenshot, and including phrasings like "build the list view", "add the detail page", "hook this up to the API", or "make the dashboard". Also use when generating or refreshing a typed API client from a spec. Prefer this skill over building a screen directly from a design; the design says nothing about data shapes.
---

# Building frontend screens from an OpenAPI spec

## Why this exists

The dominant failure mode when building UI from a design is confident invention. Given a mockup showing an "Account" column and a "Score" badge, it is very easy to write `opportunity.accountName` and `opportunity.score` — plausible names that don't exist, in a shape that never matches the response. The component compiles, renders against mock data, and breaks the moment it meets the API.

The spec is the ground truth. Read it before writing JSX, every time, even when the design seems to make the shape obvious.

## Stack

Use these unless the repo clearly says otherwise. Check `package.json` first — if it disagrees with this list, the repo wins and this section is stale.

| Concern | Use |
|---|---|
| Framework | Next.js App Router with TypeScript |
| Backend | FastAPI, separate service — not Next.js route handlers |
| Styling | Tailwind, theme values from `tailwind.config.ts` |
| Components | shadcn/ui — install via `npx shadcn@latest add <name>` rather than hand-rolling |
| Tables | TanStack Table for anything sortable, filterable, or paginated |
| Charts | Recharts, via the shadcn chart wrapper so theming stays consistent |
| Data fetching | TanStack Query over the generated client |
| Client generation | `openapi-typescript` against the FastAPI spec |
| Forms | react-hook-form with zod, schemas derived from the spec |

Don't introduce a second component library, a second charting library, or a second styling approach. Mixed conventions across screens are much harder to unwind than a slightly awkward fit on one screen.

If something here genuinely doesn't fit a requirement, say so and ask before reaching for an alternative.

## Next.js and FastAPI

FastAPI already publishes the spec, so don't maintain a copy by hand. Generate against the running server:

```bash
npx openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.d.ts
```

Wire that to `npm run api:types` and re-run it whenever a Pydantic model changes. This is the whole reason the setup is worth having — a renamed field becomes a TypeScript error instead of a runtime surprise.

FastAPI's spec quality depends entirely on the Python side. If response models are missing or a route returns a bare dict, generated types come out as `unknown` or `any`. When that happens, the fix is a `response_model` on the FastAPI route, not a hand-written type on the frontend. Say so rather than patching around it.

**Server versus client components.** Interactive data views — anything with filtering, sorting, inline actions, or optimistic updates — are client components with TanStack Query. Reach for server components for the page shell, static content, and initial reads that don't need interactivity. Don't force a filterable table into a server component; refetching the whole route on every filter change is worse than a client-side query in every way that matters here.

**The trap: business logic migrating into Next.js.** With route handlers available, it's tempting to add "just one" endpoint that aggregates or transforms data. Resist it. Logic split across two services in two languages is the single most expensive thing you can do to this codebase, and it happens one small addition at a time.

Route handlers are legitimate for exactly two jobs:

- Proxying to FastAPI so auth tokens stay in httpOnly cookies and never reach client JS
- Sidestepping CORS in development

Both are thin pass-throughs. If a route handler is reshaping data or making decisions, that behavior belongs in FastAPI.

Keep the API base URL in `NEXT_PUBLIC_API_URL` (or server-side only if everything proxies), never hardcoded.

## Step 1 — Find and read the spec

With FastAPI, the spec is served live at `/openapi.json` (docs at `/docs`). Prefer the running server over any checked-in copy, which may be stale. Pull it down to inspect:

```bash
curl -s http://localhost:8000/openapi.json > /tmp/openapi.json
```

Users sometimes refer to it as "openai.json" — same file, treat it as OpenAPI.

Large specs shouldn't be pulled into context wholesale. Get the shape first:

```bash
jq -r '.paths | keys[]' openapi.json
jq -r '.components.schemas | keys[]' openapi.json
```

Then read only the paths and schemas the current screen actually needs:

```bash
jq '.paths."/opportunities".get' openapi.json
jq '.components.schemas.Opportunity' openapi.json
```

If the spec is YAML, `yq` works the same way.

## Step 2 — Make sure a typed client exists

Check for a generated client before writing any fetch code. If there isn't one, generate it rather than hand-writing types:

```bash
npx openapi-typescript openapi.json -o src/api/schema.d.ts
```

For a full client with hooks, `orval` or `hey-api` are better than `openapi-typescript` alone.

Two rules follow from this:

- Never hand-write a request or response interface. If a type feels missing, it means the spec is incomplete or the wrong schema is being referenced — say so instead of filling the gap.
- All data access goes through the generated client. Scattered raw `fetch` calls are how field names drift back in.

When the backend changes, regenerate. A compile error at that moment is the entire point of the setup.

## Step 3 — Map the screen to endpoints before writing code

State this explicitly before any JSX, and keep it short:

- Which endpoints the screen calls, and for what
- The exact response schema name from the spec
- Which field backs each column, section, or form input
- Which fields are nullable or optional in the spec

That last one drives real UI decisions. An optional `score` means the table needs a rendering for its absence, not `undefined` leaking into a badge.

If a design element has no field behind it — a column, metric, or section that nothing in the spec supplies — stop and resolve it before writing the component.

### When a feature has no endpoint

Don't route around the gap silently, and don't invent data to fill it. Name it, then recommend one of three outcomes:

**Add the endpoint.** Correct whenever the value is an aggregate over data the screen wouldn't otherwise fetch. Computing an acceptance rate or a funnel conversion by pulling the full collection into the browser is the wrong shape — it's slow, it breaks as data grows, and it puts business logic on the wrong side of the wire. Since the backend is ours, a new route is usually cheaper than the frontend workaround. Say concretely what the route should return.

**Derive it client-side.** Fine when the inputs are already in hand for another reason. A count of rows matching a filter, in a list already fetched, needs no new endpoint.

**Cut it from the design.** If it's neither cheap to add nor derivable, and nothing depends on it, removing it beats carrying a hole.

The deciding question: is this an aggregate over data we wouldn't otherwise load? If yes, it belongs in the backend.

Two specific cases worth checking early, because they're expensive to discover late:

- **Reporting aggregates.** Funnels, conversion rates, distributions, and breakdowns each want a dedicated endpoint returning pre-computed numbers. A CRUD-only API usually has none of them.
- **Structured fields behind reporting.** If a decision endpoint accepts free text but no enumerated reason, any report grouping by reason is impossible. That's a backend model change, not a frontend problem — surface it as such.

### Never fill a gap with fake data

A component built against invented data and marked for later replacement does not get replaced. It gets demoed, and the fabrication survives into the review.

If a screen has to ship before its endpoint exists, render that section as an explicit unavailable state — a labelled placeholder saying the data isn't wired yet. Visibly missing is recoverable. Plausibly wrong is not.

## Step 4 — Build every state, not just the happy one

A data view isn't done when it renders a populated list. Every screen that fetches needs:

| State | What it needs |
|---|---|
| Loading | Skeleton matching the real layout, not a centered spinner — layout shift on load is what makes an app feel cheap |
| Empty | An invitation naming what goes here, plus the action that creates one. Not "No data." |
| Error | What failed and a retry affordance. Never a raw exception string |
| Populated | The normal case |
| Degenerate | See below — this is the one that gets skipped |

Build these as you go. Retrofitting states across a dozen finished components is far more work than including them from the start.

### Degenerate data

Designs are drawn against tidy invented data: short names, every field present, four rows. Real responses aren't. Before calling a screen done, check it against:

- A string long enough to wrap or overflow its column
- Every optional field in the schema set to null
- Zero rows
- Enough rows to need pagination or virtualization
- A number far outside the expected range (0, negative, very large)

Fixed table layouts with explicit column widths plus truncation handle most of this. Auto-sizing columns look fine in a mockup and fall apart on real data.

## Step 5 — Token discipline

Colors, spacing, radii, and type sizes come from the theme config, never hardcoded in components. A component with `#3b82f6` in it is a component that won't follow a theme change and won't survive dark mode.

If a value genuinely isn't in the theme yet, add it to the theme and then reference it.

## Working from Figma

The Figma MCP server can read a selected frame or a pasted link. Use it for layout, hierarchy, spacing, and visual language.

Do not port generated code out of Figma Make into the repo. It arrives with its own component conventions, its own styling approach, and mock data threaded through it — reconciling that with a real typed client is usually more work than rebuilding from the visual.

Two things transfer cleanly and are worth doing once, early:

1. Design variables and text styles → theme config, in a single pass before any component work
2. Layout and component structure → as reference while building

Everything else — data shapes, states, edge cases — comes from the spec, not the design.

## Conventions for dense data apps

These apply to pipeline views, admin tools, CRMs, queues, and dashboards. Skip them for marketing surfaces.

- **Tables, not card grids.** Cards look designed and are worse at scanning, comparing, and sorting, which is the actual job.
- **Color carries meaning only.** Status pills get color. Nothing else does. Once color is decorative it stops being readable as signal.
- **Tabular numerals** on any column of numbers, so digits align vertically.
- **Default the view to the actionable subset.** A queue should open on what needs action, sorted by whatever ranks urgency — not on an unfiltered list sorted by creation date.
- **Surface staleness.** If the data comes from a periodic job, show when it last ran. If items age, make age visible and let it change appearance past a threshold. A queue should reveal its own neglect.
- **Make rows keyboard-navigable** and put primary actions inline. Forcing a round trip into a detail page to act on an item is the difference between reviewing 5 and reviewing 30.

## Anti-patterns

- Writing a component before reading the endpoint that feeds it
- Hand-written interfaces duplicating what the spec already defines
- `any` used to get past a shape mismatch — the mismatch is information, not an obstacle
- Mock data left in a component after the endpoint is wired
- Filling a missing endpoint with plausible fake numbers instead of an explicit unavailable state
- Fetching a whole collection client-side to compute an aggregate that belongs in a backend route
- Optional spec fields rendered as though they're always present
- One giant component per screen; split table, row, filters, and states apart so they're separately testable
- Building all screens in parallel before the first one's patterns are reviewed — the first screen's quality propagates to every one after it