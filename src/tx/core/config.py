"""Settings. Every tunable lives here and comes from the environment.

Nothing in this service hard-codes a threshold. The values that carry a comment were measured
on a real 240-page contract; they are defaults, not constants.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---------------------------------------------------------------- service
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    data_dir: Path = REPO_ROOT / "data"
    #: v1 ships without authentication, so binding beyond localhost is opt-in and loud.
    allow_insecure_bind: bool = False

    max_upload_bytes: int = 100 * 1024 * 1024
    max_pages: int = 1000

    #: Worker runs inside the API process for local use; false when workers scale separately.
    run_worker_in_process: bool = True
    worker_poll_seconds: float = 1.0
    worker_lease_seconds: int = 900
    max_attempts: int = 3

    #: Comma-separated origins for a browser client on another port. Empty disables CORS, which
    #: is correct when the UI is served from this same app.
    cors_allow_origins: str = ""
    #: Page previews are for reading on screen, not for the model, so they render much smaller
    #: than the extraction path.
    page_image_scale: float = 2.0

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    # ---------------------------------------------------------------- retention (parked)
    result_ttl_hours: int = 0  # 0 disables expiry
    delete_upload_on_success: bool = False

    # ---------------------------------------------------------------- extraction
    extraction_mode_default: Literal["fast", "balanced", "thorough"] = "balanced"
    vision_concurrency: int = 8
    #: Render scale must leave a strip with more pixels than the API's normalisation target,
    #: otherwise the crop is an upscaled blur. Below ~3.7 a four-way strip starts losing detail.
    render_scale: float = 4.0
    rows_per_strip: int = 22
    #: Every strip is normalised to the same visual-token budget, so it is rows per image, not
    #: images per page, that decides whether a digit survives. On page 128 of the reference
    #: contract 21 rows a strip scored 29-32 of 36 hand-checked money cells and 11 scored
    #: 35-36. Errors at 21 repeated across runs, so they are a resolution limit, not sampling
    #: noise, and voting cannot fix them.
    rows_per_strip_raster: int = 11
    #: A runaway guard, not a quality knob: capping strips per page would cram more rows into
    #: each one exactly on the dense pages that can least afford it.
    max_strips: int = 24
    strip_overlap: float = 0.04
    strip_detail: Literal["auto", "low", "high"] = "high"

    #: Routing. A page earns a vision call on either signal; missing a page loses data silently,
    #: so the router is deliberately generous.
    router_min_table_rows: int = 3
    router_min_table_cols: int = 2
    router_min_short_lines: int = 30
    router_max_line_len: int = 45

    ground_identifiers: bool = True
    #: Blanking numbers the OCR leg did not see emptied 107 correct price cells on the
    #: reference contract, because on a rasterised page OCR is the weaker reader. Report instead.
    ground_numbers: bool = False
    identifier_min_similarity: float = 0.62
    identifier_margin: float = 0.08

    max_nest_depth: int = 3
    default_currency: str = "AUD"
    default_locale_hint: str = ""
    #: Share of a column's numeric cells a separator convention must explain to be accepted.
    #: Demanding all of them let two OCR slips ('1.029.05' for '1,029.05') reject every
    #: candidate, nulling 66 sound values in one column of the reference rate card.
    locale_min_agreement: float = 0.8
    suppress_min_rows: int = 3
    suppress_min_cols: int = 2
    #: A single-column block this tall is a list of real content, not a running header, so the
    #: column rule must not apply to it. Without this a 181-row equipment list is filed as
    #: furniture purely for having one column.
    suppress_min_list_rows: int = 10
    #: A short block with identical content on this many pages is a letterhead. It has to go
    #: before merging, or consecutive copies weld into a tall table that then looks like content.
    furniture_repeat_pages: int = 3

    merge_bottom_threshold: float = 0.80
    merge_top_threshold: float = 0.20
    merge_header_conflict_threshold: float = 0.95
    merge_signature_agreement: float = 0.75

    # ---------------------------------------------------------------- providers
    azure_di_endpoint: str = ""
    azure_di_key: str = ""
    azure_di_model: str = "prebuilt-layout"

    azure_openai_endpoint: str = ""
    azure_openai_key: str = ""
    azure_openai_api_version: str = "2025-04-01-preview"
    foundry_vision_deployment: str = ""
    foundry_merge_deployment: str = ""

    vision_max_tokens: int = 32000
    merge_max_tokens: int = 2000
    max_provider_attempts: int = 5
    provider_backoff_seconds: float = 1.0
    provider_backoff_max_seconds: float = 30.0

    # ---------------------------------------------------------------- backends
    job_store: Literal["sqlite"] = "sqlite"
    blob_backend: Literal["local"] = "local"
    extractor: Literal["pipeline", "null"] = "pipeline"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.db"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def ocr_configured(self) -> bool:
        return bool(self.azure_di_endpoint)

    @property
    def vision_configured(self) -> bool:
        return bool(self.azure_openai_endpoint and self.foundry_vision_deployment)

    @property
    def merge_llm_configured(self) -> bool:
        return bool(self.azure_openai_endpoint and self.foundry_merge_deployment)

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if self.host not in {"127.0.0.1", "localhost", "::1"} and not self.allow_insecure_bind:
            raise ValueError(
                f"HOST={self.host!r} exposes an unauthenticated service. "
                "Set ALLOW_INSECURE_BIND=true if that is genuinely intended."
            )
        if self.strip_overlap < 0 or self.strip_overlap >= 0.5:
            raise ValueError("STRIP_OVERLAP must be in [0, 0.5)")
        if self.rows_per_strip < 1:
            raise ValueError("ROWS_PER_STRIP must be >= 1")
        if self.rows_per_strip_raster < 1:
            raise ValueError("ROWS_PER_STRIP_RASTER must be >= 1")
        if self.max_strips < 1:
            raise ValueError("MAX_STRIPS must be >= 1")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
