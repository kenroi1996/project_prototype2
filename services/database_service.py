"""PostgreSQL database service — Star Schema + Portal Source Tables."""

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from typing import Optional, List, Dict, Any
import logging
import os

logger = logging.getLogger(__name__)


# ── Portal source table configurations ─────────────────────────────

PORTAL_SOURCE_CONFIGS = {
    "mis": {
        "table": "mis_students",
        "id_field": "id_no",
        "field_map": {
            "ID_NO": "id_no",
            "SYSTEMCODE": "systemcode",
            "PROGRAM": "program",
            "COLLEGE": "college",
            "SECCODE": "seccode",
            "YEAR": "year",
            "SEX_CODE": "sex_code",
            "HOME_ADDRESS": "home_address",
            "CIVIL_STATUS": "civil_status",
            "RELIGION": "religion",
            "FINAL_AVG_GRD": "final_avg_grd",
        },
        "numeric_fields": {"year": int, "final_avg_grd": float},
    },
    "sao": {
        "table": "sao_student_profile",
        "id_field": "student_id",
        "field_map": {
            "STUDENT_ID": "student_id",
            "SCHOLARSHIP_APPLICANT": "scholarship_applicant",
            "SCHOLARSHIP_TYPE": "scholarship_type",
            "GENDER": "gender",
            "BIRTHDATE": "birthdate",
            "MUNICIPALITY": "municipality",
            "PROGRAM": "program",
        },
        "numeric_fields": {},
        "boolean_fields": {"scholarship_applicant": lambda v: str(v).strip().lower() in ("true", "1", "yes", "t", "y", "with", "approved", "scholar")},
    },
    "registrar": {
        "table": "registrar_student_profile",
        "id_field": "student_id",
        "field_map": {
            "student_id": "student_id",
            "lastname": "lastname",
            "firstname": "firstname",
            "gender": "gender",
            "hs_gpa": "hs_gpa",
            "year_graduated": "year_graduated",
            "shs_strand": "shs_strand",
            "hs_type": "hs_type",
            "graduation_honors": "graduation_honors",
            "hs_school": "hs_school",
            "municipality": "municipality",
            "home_address": "home_address",
            "year_enrolled": "year_enrolled",
        },
        "numeric_fields": {"hs_gpa": float, "year_graduated": int, "year_enrolled": int},
    },
    "guidance": {
        "table": "guidance_student_profile",
        "id_field": "student_id",
        "field_map": {
            "Date": "exam_date",
            "student_id": "student_id",
            "systemcode": "systemcode",
            "last_name": "last_name",
            "first_name": "first_name",
            "entrance_exam_score": "entrance_exam_score",
            "family_income_bracket": "family_income_bracket",
            "parent_highest_education": "parent_highest_education",
            "applicant_age": "applicant_age",
            "home_municipality": "home_municipality",
            "program_code": "program_code",
        },
        "numeric_fields": {"entrance_exam_score": float, "applicant_age": int},
    },
}


class DatabaseService:
    def __init__(self, host=None, port=None, database=None,
                 user=None, password=None):
        self.conn_params = {
            "host": host or os.getenv("DB_HOST", "localhost"),
            "port": port or int(os.getenv("DB_PORT", "5432")),
            "database": database or os.getenv("DB_NAME", "testDB"),
            "user": user or os.getenv("DB_USER", "postgres"),
            "password": password or os.getenv("DB_PASSWORD", "admin123"),
        }
        self._conn: Optional[psycopg2.extensions.connection] = None

    def connect(self) -> bool:
        try:
            self._conn = psycopg2.connect(**self.conn_params)
            logger.info("Connected to PostgreSQL: %s", self.conn_params["database"])
            return True
        except psycopg2.Error as e:
            logger.error("Failed to connect: %s", e)
            print(f"[DB CONNECT ERROR] {e}")  # Add this
            return False
        
    def disconnect(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    @staticmethod
    def _parse_semester(semester: str | int) -> int:
        if isinstance(semester, int):
            return 1 if semester <= 1 else 2
        sem = str(semester).strip().lower()
        if sem.startswith("1"):
            return 1
        if sem.startswith("2"):
            return 2
        return 1

    def pull_data(self, portal_key: str, limit: Optional[int] = None,
                  where_clause: Optional[str] = None) -> Dict[str, Any]:
        """Pull data from source table into headers/rows format."""
        if not self._conn:
            raise RuntimeError("Not connected to database")

        config = PORTAL_SOURCE_CONFIGS.get(portal_key)
        if not config:
            return {"success": False, "error": f"Unknown portal: {portal_key}"}

        table = config["table"]
        field_map = config["field_map"]

        db_to_csv = {v: k for k, v in field_map.items()}
        columns = list(field_map.values())
        headers = list(field_map.keys())

        query = f"SELECT {', '.join(columns)} FROM {table}"
        if where_clause:
            query += f" WHERE {where_clause}"
        query += f" ORDER BY {config['id_field']}"

        if limit:
            query += f" LIMIT {limit}"

        rows = []
        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            for record in cur:
                row = [str(record[col]) if record[col] is not None else ""
                       for col in columns]
                rows.append(row)

        return {
            "success": True,
            "headers": headers,
            "rows": rows,
            "total": len(rows),
            "table": table,
        }

    # ── PUSH: Portal CSV → Source Table ──────────────────────────────

    def push_data(self, portal_key: str, headers: List[str], rows: List[List[str]]) -> Dict[str, Any]:
        """
        Push cleaned portal data to the appropriate source table.

        Row-count reconciliation
        --------------------------
        The upsert query is `INSERT ... ON CONFLICT (id_field) DO UPDATE`, so
        if the source `rows` contain the same id_field value more than once
        (e.g. a student appears twice due to a re-export or a merge artifact
        that CleaningEngine's exact-full-row "Remove Duplicates" wouldn't
        catch, since the duplicate rows differ in some other column), every
        occurrence after the first UPDATES the same DB row instead of
        inserting a new one. cur.rowcount is never negative after a
        successful execute, so a naive `if cur.rowcount >= 0: inserted += 1`
        counts "statements that ran," not "rows now in the table" — those
        numbers diverge exactly by the number of duplicate source IDs.

        This method now detects duplicate ids_field values in the SOURCE
        rows before writing, and measures the table's row count before and
        after the batch, so the two numbers (rows processed vs. rows
        actually added) are both available and explained rather than
        silently conflated into one misleading "inserted" figure.
        """
        if not self._conn:
            raise RuntimeError("Not connected to database")

        config = PORTAL_SOURCE_CONFIGS.get(portal_key)
        if not config:
            return {"success": False, "error": f"Unknown portal: {portal_key}", "inserted": 0}

        table = config["table"]
        id_field = config["id_field"]
        field_map = config["field_map"]
        numeric_fields = config.get("numeric_fields", {})
        boolean_fields = config.get("boolean_fields", {})

        # Case-insensitive header matching
        header_lookup = {h.upper().strip().replace(" ", "_"): h for h in headers}
        header_lower_lookup = {h.lower(): h for h in headers}

        col_indices = {}
        for csv_col, db_col in field_map.items():
            if csv_col in headers:
                col_indices[db_col] = headers.index(csv_col)
            elif csv_col.upper() in header_lookup:
                col_indices[db_col] = headers.index(header_lookup[csv_col.upper()])
            elif csv_col.lower() in header_lower_lookup:
                col_indices[db_col] = headers.index(header_lower_lookup[csv_col.lower()])

        print(f"[DB] portal={portal_key}, table={table}, matched_cols={list(col_indices.keys())}, rows={len(rows)}")

        if not col_indices:
            return {"success": False, "error": f"No matching columns for {portal_key}", "inserted": 0}

        # ── Detect duplicate IDs in the SOURCE data before writing ────────────
        # These are the rows that will collapse into the same DB row via
        # ON CONFLICT DO UPDATE — not an error, but worth surfacing so the
        # save-confirmation number and the actual table count can be
        # reconciled instead of silently disagreeing.
        duplicate_id_examples: List[str] = []
        seen_ids: set = set()
        if id_field in col_indices:
            id_idx = col_indices[id_field]
            for row in rows:
                raw_id = row[id_idx] if id_idx < len(row) else None
                norm_id = str(raw_id).strip() if raw_id is not None else ""
                if not norm_id:
                    continue
                if norm_id in seen_ids:
                    if len(duplicate_id_examples) < 10:
                        duplicate_id_examples.append(norm_id)
                else:
                    seen_ids.add(norm_id)
        n_duplicate_ids = len(rows) - len(seen_ids) if seen_ids else 0

        if n_duplicate_ids:
            print(
                f"[DB] WARNING: {n_duplicate_ids} row(s) in the source data share "
                f"an {id_field} value already seen earlier in this upload. "
                f"These will UPDATE the existing row via ON CONFLICT, not add a "
                f"new one — table row count will end up lower than rows pushed. "
                f"Examples: {duplicate_id_examples}"
            )

        db_cols = list(col_indices.keys())
        placeholders = ", ".join(["%s"] * len(db_cols))
        columns = ", ".join(db_cols)

        update_cols = [c for c in db_cols if c != id_field]
        if update_cols:
            upsert = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
            query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) ON CONFLICT ({id_field}) DO UPDATE SET {upsert}"
        else:
            query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

        print(f"[DB] query: {query[:120]}...")

        # ── Row count BEFORE the batch, for net-new reconciliation ────────────
        count_before = 0
        with self._conn.cursor() as cur:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count_before = cur.fetchone()[0]
            except psycopg2.Error:
                count_before = 0

        inserted = 0
        errors = []
        batch_size = 1000

        with self._conn.cursor() as cur:
            for i, row in enumerate(rows):
                values = []
                for db_col in db_cols:
                    idx = col_indices[db_col]
                    val = row[idx] if idx < len(row) else None

                    if db_col in numeric_fields:
                        try:
                            val = numeric_fields[db_col](val) if val and str(val).strip() else None
                        except (ValueError, TypeError):
                            val = None
                    elif db_col in boolean_fields:
                        val = boolean_fields[db_col](val)
                    elif val:
                        val = str(val).strip()
                    else:
                        val = None
                    values.append(val)

                try:
                    cur.execute(query, values)
                    # NOTE: cur.rowcount is never negative after a successful
                    # execute, so this counts "statements that ran without
                    # error" — inserts AND updates alike — not "new rows in
                    # the table." See count_before/actual_count below for the
                    # true net-new figure.
                    if cur.rowcount >= 0:
                        inserted += 1
                except psycopg2.Error as e:
                    errors.append(f"Row {i+1} (id={values[0] if values else 'unknown'}): {e}")
                    if len(errors) <= 3:
                        print(f"[DB ERROR] {errors[-1]}")

                # Commit every batch to avoid memory issues
                if (i + 1) % batch_size == 0:
                    self._conn.commit()
                    print(f"[DB] committed batch {i+1}")

            self._conn.commit()

        # Get actual table count
        actual_count = 0
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            actual_count = cur.fetchone()[0]

        net_new_rows = actual_count - count_before

        print(
            f"[DB] done: rows_processed={inserted}, errors={len(errors)}, "
            f"count_before={count_before}, count_after={actual_count}, "
            f"net_new_rows={net_new_rows}, duplicate_ids_in_source={n_duplicate_ids}"
        )

        return {
            "success": True,
            "inserted": inserted,
            "errors": errors,
            "total": len(rows),
            "table": table,
            "actual_db_count": actual_count,
            # ── New, more precise fields ───────────────────────────────────
            "count_before": count_before,
            "net_new_rows": net_new_rows,
            "duplicate_ids_in_source": n_duplicate_ids,
            "duplicate_id_examples": duplicate_id_examples,
        }

    def get_stats(self, portal_key: str) -> Dict[str, Any]:
        """Get source table statistics for a portal."""
        if not self._conn:
            return {"error": "Not connected to database", "total_records": 0, "table": "", "programs": []}

        config = PORTAL_SOURCE_CONFIGS.get(portal_key)
        if not config:
            return {"error": f"Unknown portal: {portal_key}", "total_records": 0, "table": "", "programs": []}

        table = config["table"]

        with self._conn.cursor() as cur:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
            except psycopg2.Error:
                count = 0

            programs = []
            if "program" in config["field_map"].values():
                try:
                    cur.execute(f"""
                        SELECT DISTINCT program FROM {table}
                        WHERE program IS NOT NULL ORDER BY program
                    """)
                    programs = [r[0] for r in cur.fetchall()]
                except psycopg2.Error:
                    pass

        return {
            "total_records": count,
            "table": table,
            "programs": programs,
        }

    def get_all_stats(self) -> Dict[str, Any]:
        """Get stats for all portal source tables."""
        return {
            key: self.get_stats(key)
            for key in PORTAL_SOURCE_CONFIGS.keys()
        }

    # ── STAR SCHEMA ETL ──────────────────────────────────────────────

    def run_star_schema_etl(self, academic_year: str = "2024-2025",
                            semester: str = "1st") -> Dict[str, Any]:
        """
        Record a merge/pipeline run in merge_log for schema v2.

        The attached schema replaces older star-schema population functions
        with a merge_log audit table and a rebuilt fact_student_risk table.
        """
        if not self._conn:
            raise RuntimeError("Not connected to database")

        try:
            sem = self._parse_semester(semester)
            with self._conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM public.mis_students")
                mis_rows = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM public.guidance_student_profile")
                guidance_rows = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM public.registrar_student_profile")
                registrar_rows = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM public.sao_student_profile")
                sao_rows = cur.fetchone()[0]
                total_students = mis_rows

                coverage_pct = 0.0
                if total_students > 0:
                    coverage_pct = round(
                        (
                            (guidance_rows + registrar_rows + sao_rows)
                            / (3 * total_students)
                        ) * 100,
                        2,
                    )

                cur.execute(
                    """
                    INSERT INTO public.merge_log (
                        academic_year, semester, total_students,
                        mis_rows, guidance_rows, registrar_rows, sao_rows,
                        unmatched_guidance, unmatched_registrar, unmatched_sao,
                        coverage_pct, model_type, accuracy, cv_mean, cv_std, notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING run_id
                    """,
                    (
                        academic_year,
                        sem,
                        total_students,
                        mis_rows,
                        guidance_rows,
                        registrar_rows,
                        sao_rows,
                        max(total_students - guidance_rows, 0),
                        max(total_students - registrar_rows, 0),
                        max(total_students - sao_rows, 0),
                        coverage_pct,
                        None,
                        None,
                        None,
                        None,
                        "Run logged from portal ETL action",
                    ),
                )
                run_id = cur.fetchone()[0]
                self._conn.commit()

                return {
                    "success": True,
                    "run_id": run_id,
                    "academic_year": academic_year,
                    "semester": sem,
                    "total_students": total_students,
                    "mis_rows": mis_rows,
                    "guidance_rows": guidance_rows,
                    "registrar_rows": registrar_rows,
                    "sao_rows": sao_rows,
                    "coverage_pct": coverage_pct,
                    "facts_inserted": 0,
                }
        except psycopg2.Error as e:
            self._conn.rollback()
            return {"success": False, "error": str(e)}

    def get_star_schema_stats(self) -> Dict[str, Any]:
        """Get counts from all star schema tables."""
        if not self._conn:
            raise RuntimeError("Not connected")

        tables = {
            "dim_risk_level": "public.dim_risk_level",
            "dim_program": "public.dim_program",
            "fact_student_risk": "public.fact_student_risk",
            "merge_log": "public.merge_log",
        }

        stats = {}
        with self._conn.cursor() as cur:
            for name, table in tables.items():
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[name] = cur.fetchone()[0]
                except psycopg2.Error:
                    stats[name] = 0

        return stats

    def get_unified_features(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Query latest student-risk summary view (schema v2)."""
        if not self._conn:
            raise RuntimeError("Not connected")

        query = "SELECT * FROM public.v_student_risk_summary"
        if limit:
            query += f" LIMIT {limit}"

        with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            return [dict(row) for row in cur.fetchall()]



    def get_active_model(self):
        """
        Fetches the current active model from the model_registry table.
        Returns a dictionary with model details or None if no active model exists.
        """
        query = """
            SELECT model_name, model_type, metadata 
            FROM public.model_registry 
            WHERE is_active = TRUE 
            ORDER BY created_at DESC 
            LIMIT 1;
        """
        try:
            # Assuming self.conn is the active connection managed by the context manager
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                row = cur.fetchone()
                # Return as a standard dict for the UI to consume
                return dict(row) if row else None
        except Exception as e:
            print(f"[DatabaseService] Error fetching active model: {e}")
            return None

    def register_model(self, name, model_type, metadata, is_active=True):
        """
        Registers a new model record into the registry.
        If is_active is True, it deactivates all other models first to ensure
        only one model is marked active at a time.
        """
        try:
            with self.conn.cursor() as cur:
                # Ensure only one model is active at a time if this one is set to active
                if is_active:
                    cur.execute("UPDATE public.model_registry SET is_active = FALSE;")
                
                query = """
                    INSERT INTO public.model_registry (model_name, model_type, metadata, is_active)
                    VALUES (%s, %s, %s, %s)
                    RETURNING model_id;
                """
                # The Json() wrapper handles Python dict -> PostgreSQL JSONB conversion
                cur.execute(query, (name, model_type, Json(metadata), is_active))
                model_id = cur.fetchone()[0]
                
                return {
                    "success": True, 
                    "id": model_id, 
                    "table": "model_registry"
                }
        except Exception as e:
            # Return error details to be logged in the UI's Activity Log
            return {"success": False, "error": str(e)}

    def ensure_schema(self):
        """Utility to ensure the registry table exists (matching your CREATE TABLE)."""
        schema_query = """
        CREATE TABLE IF NOT EXISTS public.model_registry (
            model_id SERIAL PRIMARY KEY,
            model_name VARCHAR(100) NOT NULL,
            model_type VARCHAR(50) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT FALSE,
            metadata JSONB
        );
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(schema_query)
        except Exception as e:
            print(f"[DatabaseService] Schema init error: {e}")