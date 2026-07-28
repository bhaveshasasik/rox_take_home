"""Reproducible test: does creating a column via the API ever generate cells?

Prints the exact request and response for every call, then watches
/priority_jobs and the cell values so the outcome is observable rather than
asserted.

    cd backend && .venv/bin/python -m scripts.probe_column_generation

Creates 2 columns in the org. Requires ROX_BASE_URL / ROX_API_TOKEN in .env.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx

from app.config import get_settings
from app.rox.client import flatten_hierarchy

settings = get_settings()
BASE = settings.rox_base_url


def now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def show_request(method: str, path: str, body: object = None) -> None:
    print(f"\n>>> {method} {BASE}{path}")
    print("    Authorization: Bearer rk_...redacted")
    print("    Content-Type: application/json")
    if body is not None:
        rendered = json.dumps(body, indent=2)
        if len(rendered) > 1400:
            rendered = rendered[:1400] + "\n    ...(truncated for display)"
        print(f"    body:\n{rendered}")


def show_response(resp: httpx.Response, limit: int = 700) -> object:
    print(f"<<< {resp.status_code} {resp.reason_phrase}")
    try:
        data = resp.json()
        print(f"    {json.dumps(data, default=str)[:limit]}")
        return data
    except ValueError:
        print(f"    {resp.text[:limit]}")
        return None


async def call(client, method, path, body=None, params=None):
    show_request(method, path, body)
    resp = await client.request(method, path, json=body, params=params)
    return show_response(resp)


async def jobs_snapshot(client) -> list[dict]:
    resp = await client.get("/priority_jobs")
    return resp.json() if resp.status_code == 200 else []


def summarize_jobs(jobs: list[dict]) -> dict:
    out: dict[str, int] = {}
    for j in jobs:
        key = f"{j['task_type']}/{j['current_state']}"
        out[key] = out.get(key, 0) + 1
    return out


async def watch(client, label, column_id, entities, before_ids, minutes=4):
    """Poll jobs + cells so generation (or its absence) is directly observable."""
    print(f"\n--- watching '{label}' column={column_id} for {minutes} min ---")
    ticks = int(minutes * 60 / 20)
    for i in range(ticks):
        await asyncio.sleep(20)
        jobs = await jobs_snapshot(client)
        new = [j for j in jobs if j["run_id"] not in before_ids]
        new_kinds: dict[str, int] = {}
        for j in new:
            new_kinds[j["task_type"]] = new_kinds.get(j["task_type"], 0) + 1

        filled = 0
        bulk = await client.get(f"/agents/customers_paginated/{column_id}")
        if bulk.status_code == 200 and isinstance(bulk.json(), list):
            rows = bulk.json()
            filled = sum(
                1 for r in rows if (r.get("value_structured") or {}).get("string_value")
            )
            total = len(rows)
        else:
            total = len(entities)

        print(
            f"    [{now()}] +{(i+1)*20:3}s  new_jobs={len(new)} {new_kinds or '{}'}"
            f"  cells={filled}/{total}"
        )
        if filled:
            print("\n*** CELLS GENERATED ***")
            for r in rows:
                v = (r.get("value_structured") or {}).get("string_value")
                if v:
                    print(f"    {r['domain']}: {v[:200]}")
                    break
            return True
    return False


async def main() -> None:
    headers = {
        "Authorization": f"Bearer {settings.rox_api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=60) as client:
        accounts = flatten_hierarchy((await client.get("/hierarchy/customers")).json())
        entities = [(a["_name"], a["_id"]) for a in accounts]
        print(f"org has {len(entities)} accounts; first = {entities[0][0]}")

        # ------------------------------------------------------------------
        # Step 1: let Rox author the config itself, so the payload cannot be
        # blamed on me hand-rolling something malformed.
        # ------------------------------------------------------------------
        print("\n" + "=" * 78)
        print("STEP 1  auto_config  (ask Rox to generate a valid column_config)")
        print("=" * 78)
        prompt = (
            "Score 0-10 how strongly this company shows buying signals in the last "
            "180 days (funding, leadership change, hiring surge, strategic "
            "initiative), then give a two-sentence rationale citing evidence."
        )
        cfg_resp = await call(
            client,
            "POST",
            "/research/clever_column/auto_config",
            {"user_prompt": prompt},
        )
        generated_config = (cfg_resp or {}).get("column_config")
        if not generated_config:
            print("!! auto_config returned no column_config; aborting")
            return
        generated_config["name"] = f"PROBE manual {now()}"
        print(f"\n    -> using Rox's own config, renamed to {generated_config['name']!r}")

        # ------------------------------------------------------------------
        # Step 2: POST /research/clever_column with THAT config
        # ------------------------------------------------------------------
        print("\n" + "=" * 78)
        print("STEP 2  POST /research/clever_column")
        print("=" * 78)
        before = {j["run_id"] for j in await jobs_snapshot(client)}
        print(f"    priority_jobs before: {len(before)}")

        created = await call(
            client,
            "POST",
            "/research/clever_column",
            {"org_wide": True, "hidden": False, "column_config": generated_config},
        )
        manual_id = (created or {}).get("column_id")

        jobs_after = await jobs_snapshot(client)
        new_now = [j for j in jobs_after if j["run_id"] not in before]
        print(f"\n    priority_jobs immediately after: {len(jobs_after)}")
        print(f"    NEW jobs from this create: {len(new_now)} {summarize_jobs(new_now)}")

        # ------------------------------------------------------------------
        # Step 3: auto_create
        # ------------------------------------------------------------------
        print("\n" + "=" * 78)
        print("STEP 3  POST /research/clever_column/auto_create")
        print("=" * 78)
        before2 = {j["run_id"] for j in await jobs_snapshot(client)}
        print(f"    priority_jobs before: {len(before2)}")

        auto = await call(
            client,
            "POST",
            "/research/clever_column/auto_create",
            {"user_prompt": prompt, "org_wide": True},
        )
        auto_id = (auto or {}).get("column_id")

        jobs_after2 = await jobs_snapshot(client)
        new_now2 = [j for j in jobs_after2 if j["run_id"] not in before2]
        print(f"\n    priority_jobs immediately after: {len(jobs_after2)}")
        print(f"    NEW jobs from auto_create: {len(new_now2)} {summarize_jobs(new_now2)}")

        # ------------------------------------------------------------------
        # Step 4: also try refresh_by_tab on each, then watch both
        # ------------------------------------------------------------------
        print("\n" + "=" * 78)
        print("STEP 4  refresh_by_tab on both new columns")
        print("=" * 78)
        org_id = "b2a7ec35-8d8e-4e35-9355-8c29a5220a3f"
        for label, cid in (("manual", manual_id), ("auto_create", auto_id)):
            if cid:
                await call(
                    client,
                    "POST",
                    f"/research/clever_column/{cid}/refresh_by_tab/{org_id}",
                    {},
                )

        watch_before = {j["run_id"] for j in await jobs_snapshot(client)}
        results = {}
        for label, cid in (("manual", manual_id), ("auto_create", auto_id)):
            if cid:
                results[label] = await watch(
                    client, label, cid, entities, watch_before, minutes=3
                )

        # ------------------------------------------------------------------
        # Control: the same refresh on a UI-created column
        # ------------------------------------------------------------------
        print("\n" + "=" * 78)
        print("CONTROL  refresh_by_tab on 'Opportunity Signal' (UI-created)")
        print("=" * 78)
        ui_col = "ece93caa-a27b-47b1-9c5c-15925cb662aa"
        ctrl_before = {j["run_id"] for j in await jobs_snapshot(client)}
        await call(
            client, "POST", f"/research/clever_column/{ui_col}/refresh_by_tab/{org_id}", {}
        )
        await asyncio.sleep(25)
        ctrl_jobs = await jobs_snapshot(client)
        ctrl_new = [j for j in ctrl_jobs if j["run_id"] not in ctrl_before]
        print(f"\n    NEW jobs from control refresh: {len(ctrl_new)} {summarize_jobs(ctrl_new)}")

        print("\n" + "=" * 78)
        print("RESULT")
        print("=" * 78)
        print(f"  POST /research/clever_column  -> column {manual_id}")
        print(f"      cells generated: {results.get('manual', False)}")
        print(f"  POST .../auto_create          -> column {auto_id}")
        print(f"      cells generated: {results.get('auto_create', False)}")
        print(f"  CONTROL (UI-created column)   -> {len(ctrl_new)} jobs enqueued")


if __name__ == "__main__":
    asyncio.run(main())
