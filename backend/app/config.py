from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Rox API
    rox_base_url: str = "https://core.roxai.dev/api/v1"
    rox_api_token: str = ""
    #: used as the refresh_by_tab `tab_id` meaning "all accounts".
    #: auto-discovered from /priorities when blank.
    rox_org_id: str = ""
    #: comma-separated Rox column names; overrides the registry for experiments
    research_columns: str = ""
    #: write real DRAFT sequences to Rox on accept
    rox_writes_enabled: bool = True

    # LLM rationale expansion
    anthropic_api_key: str = ""

    # Structured signal extraction (app/signals)
    #: gate for wiring extraction into automatic paths. The admin trigger
    #: ignores it — that endpoint is the deliberate manual escape hatch.
    extraction_enabled: bool = False
    extraction_model: str = "claude-opus-5"
    #: bump to force re-extraction under a changed schema; rows carry the
    #: version they were written at, so the two never get compared
    extraction_schema_version: int = 1

    # App
    app_base_url: str = "http://localhost:3000"
    database_url: str = "sqlite+aiosqlite:///./rox_pipeline.db"
    log_level: str = "INFO"

    # Research cycle
    research_interval_minutes: int = 15
    research_run_on_startup: bool = False
    #: Set false to keep the API up while stopping the recurring cycle. Needed
    #: for calibration work: the cycle writes 21 new artifacts every interval
    #: and creates opportunities off the old scoring path, so any before/after
    #: comparison drifts underneath you while it runs.
    research_scheduler_enabled: bool = True
    #: trigger regeneration via refresh_by_tab before reading
    research_refresh_enabled: bool = True
    #: ceiling for waiting on CUSTOM_CELL_GENERATION jobs (~90s per cell)
    job_poll_timeout_seconds: int = 600
    #: how long to look for jobs after a refresh before concluding none were
    #: scheduled (they appear within ~20s on the live API)
    job_discovery_attempts: int = 6
    job_discovery_interval_seconds: float = 5.0
    job_poll_interval_seconds: float = 10.0
    #: contacts to prospect per accepted opportunity
    contacts_per_opportunity: int = 3
    #: bound on concurrent per-account cell fetches during the bulk read
    research_concurrency: int = 8

    # Opportunity generation
    opportunity_score_threshold: int = 60
    opportunity_cooldown_days: int = 7
    #: only opportunities at/above this score get the LLM rationale expansion
    llm_expansion_score_threshold: int = 85

    # Notifications
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    notify_email_to: str = ""
    #: opportunities queue until this many are pending, then go out as one email
    notification_batch_size: int = 5

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_username and self.smtp_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
