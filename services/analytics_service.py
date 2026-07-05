"""
services/analytics_service.py
================================
All QThread workers for the Analytics page.
Pure backend — no PyQt6.QtWidgets imports.

Workers
-------
AnalyticsLoader   — runs all 6 Level-1 queries in one DB round-trip and
                    emits a single dict so the page only needs one worker.

Query outputs (keys in the emitted dict):
  primary_factor_freq   list[dict]  factor, count
  municipality_risk     list[dict]  municipality, total, high, moderate, rate
  hs_type_risk          list[dict]  hs_type, total, high, moderate, rate
  income_risk           list[dict]  bracket, total, high, moderate, rate
  intervention_coverage dict        term_ay, term_sem, high_risk_total,
                                    intervened, coverage_pct
  term_comparison       list[dict]  term_label, high, moderate, low, total

Fixes vs previous version
--------------------------
  1. HAVING COUNT(*) >= 5 lowered to >= 1 so small test cohorts aren't hidden.
  2. term_comparison now respects the ay/sem filter (was missing {where}).
  3. AnalyticsTermLoader falls back to dim_academic_term directly so the
     filter bar populates even before any predictions exist.
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from services.data_store import DataStore


# ══════════════════════════════════════════════════════════════════════════════
# Single loader — all analytics in one pass
# ══════════════════════════════════════════════════════════════════════════════

class AnalyticsLoader(QThread):
    """
    Runs all six Level-1 analytics queries against the DB.
    Emits finished(dict) with all results on success.
    Emits error(str) on any failure.

    Optional filters
    ----------------
    ay  : str   academic year  ("2023-2024" or "" for all)
    sem : int   semester       (1, 2, or 0 for all)
    """
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, ay: str = "", sem: int = 0):
        super().__init__()
        self._ay  = ay
        self._sem = sem

    # ── helpers ───────────────────────────────────────────────────────

    def _where(self) -> tuple[str, list]:
        """Build WHERE clause for optional term filter."""
        clauses, params = [], []
        if self._ay:
            clauses.append("t.academic_year = %s")
            params.append(self._ay)
        if self._sem:
            clauses.append("t.semester = %s")
            params.append(self._sem)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def _term_join(self) -> str:
        return """
            JOIN public.dim_academic_term t
                 ON t.term_key = fsr.term_key
        """

    # ── main ──────────────────────────────────────────────────────────

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            result = {}
            where, params = self._where()

            with conn.cursor() as cur:

                # ── 1. Primary factor frequency ───────────────────────
                cur.execute(f"""
                    SELECT
                        COALESCE(fsr.primary_factor, 'Unknown') AS factor,
                        COUNT(*)                                 AS cnt
                    FROM  public.fact_student_academic_risk fsr
                    {self._term_join()}
                    {where}
                    GROUP BY fsr.primary_factor
                    ORDER BY cnt DESC
                    LIMIT 10
                """, params)
                result["primary_factor_freq"] = [
                    {"factor": r[0], "count": int(r[1])}
                    for r in cur.fetchall()
                ]

                # ── 2. Municipality risk rate ─────────────────────────
                # FIX: HAVING lowered to >= 1 so small/test cohorts aren't
                #      silently dropped.  Production admins can raise this
                #      later if needed.
                # FIX: normalize known name variants (e.g. "Bogo City" →
                #      "Bogo") via CASE so they group as one municipality
                #      at the DB level, before HAVING/LIMIT are applied.
                #      Keep this list in sync with
                #      ui/components/municipality_risk_map.py's
                #      _MUNICIPALITY_ALIASES — that module is the
                #      canonical source and also re-merges defensively
                #      at the page level in case new variants appear
                #      that haven't been added here yet.
                cur.execute(f"""
                    SELECT
                        CASE LOWER(TRIM(COALESCE(NULLIF(TRIM(ds.home_municipality), ''), 'Unknown')))
                            WHEN 'bogo city'         THEN 'Bogo'
                            WHEN 'bogo, cebu'        THEN 'Bogo'
                            WHEN 'city of bogo'      THEN 'Bogo'
                            WHEN 'lapu-lapu city'    THEN 'Lapu-Lapu'
                            WHEN 'city of lapu-lapu' THEN 'Lapu-Lapu'
                            WHEN 'cebu city, cebu'   THEN 'Cebu City'
                            WHEN 'city of cebu'      THEN 'Cebu City'
                            WHEN 'danao city'        THEN 'Danao'
                            WHEN 'city of danao'     THEN 'Danao'
                            WHEN 'carcar city'       THEN 'Carcar'
                            WHEN 'city of carcar'    THEN 'Carcar'
                            WHEN 'toledo city'       THEN 'Toledo'
                            WHEN 'city of toledo'    THEN 'Toledo'
                            WHEN 'naga city'         THEN 'Naga'
                            WHEN 'city of naga'      THEN 'Naga'
                            WHEN 'talisay city'      THEN 'Talisay'
                            WHEN 'city of talisay'   THEN 'Talisay'
                            WHEN 'mandaue city'      THEN 'Mandaue'
                            WHEN 'city of mandaue'   THEN 'Mandaue'
                            WHEN 'sta. fe'           THEN 'Santa Fe'
                            WHEN 'sta fe'            THEN 'Santa Fe'
                            -- ELSE: normalize casing so "Bogo"/"BOGO"/"bogo"
                            -- group together even without an explicit alias.
                            -- INITCAP keeps hyphenated names readable
                            -- (Postgres' INITCAP already title-cases each
                            -- hyphen-delimited segment correctly).
                            ELSE INITCAP(COALESCE(NULLIF(TRIM(ds.home_municipality), ''), 'Unknown'))
                        END                                  AS municipality,
                        COUNT(*)                            AS total,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%high%%'
                        )                                   AS high_risk,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%moderate%%'
                               OR rl.risk_label ILIKE '%%medium%%'
                        )                                   AS moderate_risk
                    FROM  public.fact_student_academic_risk fsr
                    {self._term_join()}
                    JOIN  public.dim_student ds
                          ON ds.student_key = fsr.student_key
                    LEFT JOIN public.dim_risk_level rl
                          ON rl.risk_level_id = fsr.risk_level_id
                    {where}
                    GROUP BY municipality
                    HAVING COUNT(*) >= 1
                    ORDER BY (
                        COUNT(*) FILTER (WHERE rl.risk_label ILIKE '%%high%%') +
                        COUNT(*) FILTER (WHERE rl.risk_label ILIKE '%%moderate%%'
                                           OR  rl.risk_label ILIKE '%%medium%%')
                    ) DESC
                    LIMIT 20
                """, params)
                rows = cur.fetchall()
                result["municipality_risk"] = [
                    {
                        "municipality": r[0],
                        "total":        int(r[1]),
                        "high":         int(r[2]),
                        "moderate":     int(r[3]),
                        "rate":         round((r[2] + r[3]) / max(r[1], 1) * 100, 1),
                    }
                    for r in rows
                ]

                # ── 3. HS Type vs risk ────────────────────────────────
                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(ds.hs_type, ''), 'Unknown') AS hs_type,
                        COUNT(*)                                     AS total,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%high%%'
                        )                                            AS high_risk,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%moderate%%'
                               OR rl.risk_label ILIKE '%%medium%%'
                        )                                            AS moderate_risk
                    FROM  public.fact_student_academic_risk fsr
                    {self._term_join()}
                    JOIN  public.dim_student ds
                          ON ds.student_key = fsr.student_key
                    LEFT JOIN public.dim_risk_level rl
                          ON rl.risk_level_id = fsr.risk_level_id
                    {where}
                    GROUP BY ds.hs_type
                    ORDER BY total DESC
                """, params)
                rows = cur.fetchall()
                result["hs_type_risk"] = [
                    {
                        "hs_type":  r[0],
                        "total":    int(r[1]),
                        "high":     int(r[2]),
                        "moderate": int(r[3]),
                        "rate":     round((r[2] + r[3]) / max(r[1], 1) * 100, 1),
                    }
                    for r in rows
                ]

                # ── 4. Income bracket vs risk ─────────────────────────
                _INCOME_ORDER = [
                    "Below 10k", "10k-20k", "20k-40k",
                    "40k-80k",   "80k-160k", "Above 160k",
                ]
                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(ds.family_income_bracket, ''), 'Unknown')
                                                            AS bracket,
                        COUNT(*)                            AS total,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%high%%'
                        )                                   AS high_risk,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%moderate%%'
                               OR rl.risk_label ILIKE '%%medium%%'
                        )                                   AS moderate_risk
                    FROM  public.fact_student_academic_risk fsr
                    {self._term_join()}
                    JOIN  public.dim_student ds
                          ON ds.student_key = fsr.student_key
                    LEFT JOIN public.dim_risk_level rl
                          ON rl.risk_level_id = fsr.risk_level_id
                    {where}
                    GROUP BY ds.family_income_bracket
                    ORDER BY total DESC
                """, params)
                rows = cur.fetchall()
                income_data = [
                    {
                        "bracket":  r[0],
                        "total":    int(r[1]),
                        "high":     int(r[2]),
                        "moderate": int(r[3]),
                        "rate":     round((r[2] + r[3]) / max(r[1], 1) * 100, 1),
                    }
                    for r in rows
                ]

                def _income_sort(d):
                    try:
                        return _INCOME_ORDER.index(d["bracket"])
                    except ValueError:
                        return 99

                result["income_risk"] = sorted(income_data, key=_income_sort)

                # ── 5. Intervention coverage rate ─────────────────────
                cur.execute("""
                    SELECT
                        t.academic_year,
                        t.semester,
                        COUNT(DISTINCT fsr.student_key)          AS high_risk_total,
                        COUNT(DISTINCT i.student_id)             AS intervened
                    FROM  public.fact_student_academic_risk fsr
                    JOIN  public.dim_academic_term t
                          ON t.term_key = fsr.term_key
                    JOIN  public.dim_risk_level rl
                          ON rl.risk_level_id = fsr.risk_level_id
                         AND rl.risk_label ILIKE '%%high%%'
                    JOIN  public.dim_student ds
                          ON ds.student_key = fsr.student_key
                    LEFT JOIN public.interventions i
                          ON  i.student_id    = ds.student_id
                         AND  i.academic_year = t.academic_year
                         AND  i.semester      = t.semester
                         AND  i.mode          = 'per_student'
                    GROUP BY t.academic_year, t.semester, t.term_key
                    ORDER BY t.term_key DESC
                    LIMIT 1
                """)
                cov = cur.fetchone()
                if cov:
                    total_hr = int(cov[2])
                    interv   = int(cov[3])
                    result["intervention_coverage"] = {
                        "term_ay":         cov[0],
                        "term_sem":        cov[1],
                        "high_risk_total": total_hr,
                        "intervened":      interv,
                        "coverage_pct":    round(interv / max(total_hr, 1) * 100, 1),
                    }
                else:
                    result["intervention_coverage"] = {}

                # ── 6. Term comparison (grouped bar) ──────────────────
                # FIX: was missing {where}/{params} so always returned all
                #      terms regardless of the AY/sem filter selection.
                cur.execute(f"""
                    SELECT
                        COALESCE(t.term_label,
                                 t.academic_year || ' S' || t.semester::text)
                                                        AS term_label,
                        t.academic_year,
                        t.semester,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%high%%'
                        )                               AS high,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%moderate%%'
                               OR rl.risk_label ILIKE '%%medium%%'
                        )                               AS moderate,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label NOT ILIKE '%%high%%'
                              AND rl.risk_label NOT ILIKE '%%moderate%%'
                              AND rl.risk_label NOT ILIKE '%%medium%%'
                        )                               AS low,
                        COUNT(*)                        AS total
                    FROM  public.fact_student_academic_risk fsr
                    {self._term_join()}
                    LEFT JOIN public.dim_risk_level rl
                          ON rl.risk_level_id = fsr.risk_level_id
                    {where}
                    GROUP BY t.term_key, t.term_label,
                             t.academic_year, t.semester
                    ORDER BY t.term_key
                """, params)
                result["term_comparison"] = [
                    {
                        "term_label": r[0],
                        "ay":         r[1],
                        "sem":        r[2],
                        "high":       int(r[3] or 0),
                        "moderate":   int(r[4] or 0),
                        "low":        int(r[5] or 0),
                        "total":      int(r[6] or 0),
                    }
                    for r in cur.fetchall()
                ]

            self.finished.emit(result)

        except Exception as exc:
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# Term list loader
# ══════════════════════════════════════════════════════════════════════════════

class AnalyticsTermLoader(QThread):
    """
    Load distinct (academic_year, semester) pairs for the filter bar.

    FIX: queries dim_academic_term directly (with a LEFT JOIN to check
    existence) so the filter bar populates as soon as terms exist, even
    before any predictions have been run.  Falls back gracefully to an
    empty list if the table is empty.
    """
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No DB connection.")
            return
        try:
            with conn.cursor() as cur:
                # Primary: terms that actually have prediction rows
                cur.execute("""
                    SELECT DISTINCT t.academic_year, t.semester
                    FROM   public.fact_student_academic_risk fsr
                    JOIN   public.dim_academic_term t
                           ON t.term_key = fsr.term_key
                    ORDER  BY t.academic_year DESC, t.semester DESC
                """)
                rows = cur.fetchall()

                # Fallback: if no predictions yet, still show available terms
                # so the combo-box isn't stuck empty
                if not rows:
                    cur.execute("""
                        SELECT DISTINCT academic_year, semester
                        FROM   public.dim_academic_term
                        ORDER  BY academic_year DESC, semester DESC
                    """)
                    rows = cur.fetchall()

                self.finished.emit(rows)
        except Exception as exc:
            self.error.emit(str(exc))