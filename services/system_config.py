"""
services/system_config.py
==========================
Singleton that caches the public.system_config table in memory.
All pages read from this — never hardcode AY/semester strings again.

Usage
-----
    from services.system_config import SystemConfig

    ay  = SystemConfig.academic_year()   # "2024-2025"
    sem = SystemConfig.semester()        # 1  (int)
    lbl = SystemConfig.semester_label()  # "1st Semester"
    lbl_short = SystemConfig.term_label()  # "1st Semester AY 2024-25"
    inst = SystemConfig.institution()    # "CTU-Daanbantayan"

    # After saving new values to DB:
    SystemConfig.reload(conn)            # refreshes cache + notifies DataStore
"""
from __future__ import annotations

_DEFAULTS = {
    "institution_name":       "CTU-Daanbantayan",
    "default_academic_year":  "2024-2025",
    "default_semester":       "1",
    "risk_high_threshold":    "50",
    "risk_moderate_threshold":"25",
    "ollama_url":             "http://localhost:11434",
    "ollama_model":           "qwen3:4b",
}

_cache: dict[str, str] = dict(_DEFAULTS)


class SystemConfig:
    """Static accessor — no instantiation needed."""

    # ── Read ──────────────────────────────────────────────────────────

    @staticmethod
    def get(key: str, fallback: str = "") -> str:
        return _cache.get(key, _DEFAULTS.get(key, fallback))

    @staticmethod
    def institution() -> str:
        return _cache.get("institution_name", _DEFAULTS["institution_name"])

    @staticmethod
    def academic_year() -> str:
        return _cache.get("default_academic_year",
                          _DEFAULTS["default_academic_year"])

    @staticmethod
    def semester() -> int:
        try:
            return int(_cache.get("default_semester",
                                  _DEFAULTS["default_semester"]))
        except ValueError:
            return 1

    @staticmethod
    def semester_label() -> str:
        return "1st Semester" if SystemConfig.semester() == 1 else "2nd Semester"

    @staticmethod
    def semester_label_short() -> str:
        """e.g. 'Sem 1'"""
        return f"Sem {SystemConfig.semester()}"

    @staticmethod
    def term_label() -> str:
        """e.g. '1st Semester AY 2024-25'"""
        ay    = SystemConfig.academic_year()
        sem   = SystemConfig.semester_label()
        # Abbreviate "2024-2025" → "2024-25" for pill labels
        parts = ay.split("-")
        ay_short = f"{parts[0]}-{parts[1][2:]}" if len(parts) == 2 else ay
        return f"{sem} AY {ay_short}"

    @staticmethod
    def term_label_full() -> str:
        """e.g. '1st Semester · Academic Year 2024-2025'"""
        return (f"{SystemConfig.semester_label()}  ·  "
                f"Academic Year {SystemConfig.academic_year()}")

    @staticmethod
    def ollama_url() -> str:
        return _cache.get("ollama_url", _DEFAULTS["ollama_url"])

    @staticmethod
    def ollama_model() -> str:
        return _cache.get("ollama_model", _DEFAULTS["ollama_model"])

    @staticmethod
    def risk_high_threshold() -> int:
        try:
            return int(_cache.get("risk_high_threshold",
                                  _DEFAULTS["risk_high_threshold"]))
        except ValueError:
            return 50

    @staticmethod
    def risk_moderate_threshold() -> int:
        try:
            return int(_cache.get("risk_moderate_threshold",
                                  _DEFAULTS["risk_moderate_threshold"]))
        except ValueError:
            return 25

    # ── Load / Reload ─────────────────────────────────────────────────

    @staticmethod
    def load(conn) -> None:
        """
        Load config from DB into the in-memory cache.
        Safe to call on app startup (creates the table if missing).
        """
        global _cache
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS public.system_config (
                        key        VARCHAR(100) PRIMARY KEY,
                        value      TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_by VARCHAR(100)
                    )
                """)
                conn.commit()
                cur.execute("SELECT key, value FROM public.system_config")
                rows = cur.fetchall()

            db_values = {k: v for k, v in rows}
            # Merge with defaults so missing keys always have a value
            _cache = {**_DEFAULTS, **db_values}
            print(f"[SystemConfig] Loaded: AY={SystemConfig.academic_year()}, "
                  f"Sem={SystemConfig.semester()}, "
                  f"Inst={SystemConfig.institution()}")
        except Exception as exc:
            print(f"[SystemConfig] Load failed (using defaults): {exc}")
            _cache = dict(_DEFAULTS)

    @staticmethod
    def reload(conn) -> None:
        """
        Reload from DB and notify DataStore so all listening pages
        update their labels without a restart.
        """
        SystemConfig.load(conn)
        try:
            from services.data_store import DataStore
            DataStore.get()._notify("system_config")
        except Exception as exc:
            print(f"[SystemConfig] Notify failed: {exc}")

    @staticmethod
    def set_cache(key: str, value: str) -> None:
        """Update a single key in the in-memory cache (no DB write)."""
        _cache[key] = value