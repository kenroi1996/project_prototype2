from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ── Load .env from project root (works regardless of cwd) ────────────────────
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


def _require(key: str) -> str:
    """Return env var or raise a clear error if missing."""
    value = os.getenv(key)
    if value is None:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Add it to your .env file."
        )
    return value


def _optional(key: str, default: str) -> str:
    return os.getenv(key, default)


# ── Database ──────────────────────────────────────────────────────────────────
DB_HOST:     str = _optional("DB_HOST",     "localhost")
DB_PORT:     int = int(_optional("DB_PORT", "5432"))
DB_NAME:     str = _optional("DB_NAME",     "testDB")
DB_USER:     str = _optional("DB_USER",     "postgres")
DB_PASSWORD: str = _optional("DB_PASSWORD", "")

# SQLAlchemy URL — prefer an explicit DATABASE_URL override, otherwise build it
DATABASE_URL: str = _optional(
    "DATABASE_URL",
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
)

# ── ML ────────────────────────────────────────────────────────────────────────
AT_RISK_THRESHOLD: float = float(_optional("AT_RISK_THRESHOLD", "3.0"))
MODEL_PATH: Path = Path(_optional("MODEL_PATH", "ml/saved_models/xgb_model.json"))

# ── Map ───────────────────────────────────────────────────────────────────────
CAMPUS_LAT: float = float(_optional("CAMPUS_LAT", "10.3157"))
CAMPUS_LNG: float = float(_optional("CAMPUS_LNG", "123.8854"))
