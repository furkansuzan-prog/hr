import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

# Per-process pool. Total connections = workers x (pool_size + max_overflow) must stay below the server's
# max_connections; production deployments should front PostgreSQL with PgBouncer (transaction pooling).
engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    pool_size=int(os.getenv("HRSWI_DB_POOL_SIZE", "8")),
    max_overflow=int(os.getenv("HRSWI_DB_MAX_OVERFLOW", "4")),
    pool_timeout=int(os.getenv("HRSWI_DB_POOL_TIMEOUT", "30")),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
