"""Central configuration. All settings come from environment variables (12-factor)."""
import os
from functools import lru_cache

from pydantic import BaseModel

_DEV_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseModel):
    env: str = os.getenv("HRSWI_ENV", "development")
    database_url: str = os.getenv("HRSWI_DATABASE_URL", "postgresql+psycopg://hrswi:hrswi@localhost:5432/hrswi")
    jwt_secret: str = os.getenv("HRSWI_JWT_SECRET", _DEV_SECRET)
    jwt_ttl_minutes: int = int(os.getenv("HRSWI_JWT_TTL_MINUTES", "60"))
    # HMAC key used to pseudonymise national identity numbers; raw IDs are never stored.
    identity_hmac_key: str = os.getenv("HRSWI_IDENTITY_HMAC_KEY", "dev-only-identity-key")
    min_group_size: int = int(os.getenv("HRSWI_MIN_GROUP_SIZE", "5"))
    survey_min_group_size: int = int(os.getenv("HRSWI_SURVEY_MIN_GROUP_SIZE", "10"))
    forecast_min_points: int = int(os.getenv("HRSWI_FORECAST_MIN_POINTS", "12"))
    llm_enabled: bool = os.getenv("HRSWI_LLM_ENABLED", "false").lower() == "true"
    cors_origins: list[str] = os.getenv("HRSWI_CORS_ORIGINS", "http://localhost:3000").split(",")
    import_chunk_size: int = int(os.getenv("HRSWI_IMPORT_CHUNK_SIZE", "1000"))
    max_upload_mb: int = int(os.getenv("HRSWI_MAX_UPLOAD_MB", "50"))
    # B-20 / ADR-020: data-feed coverage gate mode per feed type - "off" | "warn" | "enforce".
    # Comma-separated "feed=mode" pairs; "default=" sets the rest. Decision D-13 (PO-6): the safety feed is enforced
    # (undeclared / uncovered safety data -> insufficient_data), every other feed warns (value kept, colour suppressed
    # with status_reason low_coverage). Moving the other feeds to enforce is a separate decision (needs WP-6 K3 first).
    coverage_modes: str = os.getenv("HRSWI_COVERAGE_MODES", "default=warn,safety=enforce")

    def validate_for_runtime(self) -> None:
        if self.env == "production":
            if self.jwt_secret == _DEV_SECRET or len(self.jwt_secret) < 32:
                raise RuntimeError("HRSWI_JWT_SECRET must be set to a strong value in production")
            if self.identity_hmac_key.startswith("dev-only"):
                raise RuntimeError("HRSWI_IDENTITY_HMAC_KEY must be set in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
