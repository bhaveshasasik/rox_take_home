import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import init_db
from app.logging_config import configure_logging, get_logger
from app.routers import accounts, admin, opportunities, prospecting, reporting
from app.scheduler import (
    get_scheduler,
    scheduled_research_cycle,
    shutdown_scheduler,
    start_scheduler,
)
from app.schemas import HealthOut

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    await init_db()

    # Reap before the scheduler starts, so run health never shows an orphaned
    # "running" row alongside a live cycle. At daily cadence a run lost to a
    # process death costs the whole day — if it was today's scheduled run and
    # nothing has succeeded since, run the catch-up now rather than waiting
    # for tomorrow's fire.
    from app.db import SessionLocal
    from app.services.research import needs_same_day_retry, reap_stuck_runs

    async with SessionLocal() as session:
        reaped = await reap_stuck_runs(session)
        retry = settings.research_scheduler_enabled and await needs_same_day_retry(
            session, reaped
        )

    start_scheduler()

    if retry:
        log.warning("today's scheduled run was reaped — running catch-up now")
        asyncio.create_task(scheduled_research_cycle())
    if settings.research_run_on_startup:
        asyncio.create_task(scheduled_research_cycle())

    log.info("startup complete", rox_base_url=settings.rox_base_url)
    yield
    shutdown_scheduler()
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Rox Opportunity Pipeline",
        description=(
            "End-to-end Opportunity -> Qualified pipeline: periodic research, "
            "scored opportunities, human accept/reject, automated prospecting, "
            "and pipeline reporting."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list({settings.app_base_url, "http://localhost:3000"}),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(accounts.router)
    app.include_router(opportunities.router)
    app.include_router(prospecting.router)
    app.include_router(reporting.router)
    app.include_router(admin.router)

    @app.get("/health", tags=["meta"], response_model=HealthOut)
    async def health() -> dict:
        scheduler = get_scheduler()
        job = scheduler.get_job("research_cycle") if scheduler else None
        return {
            "status": "ok",
            "scheduler_running": bool(scheduler and scheduler.running),
            "next_research_run": str(job.next_run_time) if job else None,
            "email_enabled": settings.email_enabled,
            "rox_token_configured": bool(settings.rox_api_token),
        }

    return app


app = create_app()
