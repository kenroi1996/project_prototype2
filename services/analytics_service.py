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
  avg_hs_gpa             dict        average (float | None), count
  shs_strand_risk        list[dict]  strand, total, high, moderate, rate

Fixes vs previous version
--------------------------
  1. HAVING COUNT(*) >= 5 lowered to >= 1 so small test cohorts aren't hidden.
  2. term_comparison now respects the ay/sem filter (was missing {where}).
  3. AnalyticsTermLoader falls back to dim_academic_term directly so the
     filter bar populates even before any predictions exist.

Advanced filters (this version)
---------------------------------
AnalyticsLoader now accepts risk_level / college / program / gender
filters in addition to ay/sem:
  risk_level : "" | "high" | "moderate" | "low"
  college    : "" | exact dim_program.college value
  program    : "" | exact dim_program.program_name value
  gender     : "" | exact dim_student.sex_code value (populated
               dynamically in the dropdown — see AnalyticsTermLoader —
               since the actual stored format ("M"/"F" vs "Male"/
               "Female" etc.) isn't assumed here)

dim_program (via fsr.program_key) and dim_risk_level (via
fsr.risk_level_id) are now LEFT JOINed unconditionally into every query
that supports these filters (queries 1-4 and 6), so the WHERE clause can
reference them regardless of which filters are actually active. This is
a deliberate "always join, conditionally filter" design — simpler and
more robust than toggling joins in/out of the SQL string, and the LEFT
JOINs are inexpensive since program_key/risk_level_id are 1:1 lookups.

Query 5 (intervention_coverage) is intentionally NOT extended — it
already ignores the ay/sem filter by design (always reports the single
most-recent term), so adding these three filters there would be
inconsistent with its existing behavior rather than an improvement.

Distance-from-campus is NOT a backend filter — dist_km is computed
client-side in ui/pages/analytics_page.py after the municipality rows
load, so that filter is applied there with zero DB round-trip.
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
    ay          : str   academic year  ("2023-2024" or "" for all)
    sem         : int   semester       (1, 2, or 0 for all)
    risk_level  : str   "" | "high" | "moderate" | "low"
    college     : str   "" | exact dim_program.college value
    program     : str   "" | exact dim_program.program_name value
    """
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, ay: str = "", sem: int = 0,
                 risk_level: str = "", college: str = "", program: str = "",
                 gender: str = ""):
        super().__init__()
        self._ay         = ay
        self._sem        = sem
        self._risk_level = (risk_level or "").strip().lower()
        self._college    = college or ""
        self._program    = program or ""
        self._gender     = gender or ""

    # ── helpers ───────────────────────────────────────────────────────

    def _where(self) -> tuple[str, list]:
        """
        Build WHERE clause for term filter + advanced filters (risk
        level, college, program). Assumes the query already LEFT JOINs
        dim_risk_level AS rl and dim_program AS dp when risk_level /
        college / program are referenced — see _RL_JOIN / _DP_JOIN below.
        """
        clauses, params = [], []
        if self._ay:
            clauses.append("t.academic_year = %s")
            params.append(self._ay)
        if self._sem:
            clauses.append("t.semester = %s")
            params.append(self._sem)

        # risk_level wildcards go in the PARAM values, not the SQL text,
        # so no %% doubling is needed here (unlike the hardcoded ILIKE
        # patterns already in the queries below).
        rl_filters = {
            "high":     ("rl.risk_label ILIKE %s", ["%high%"]),
            "moderate": ("(rl.risk_label ILIKE %s OR rl.risk_label ILIKE %s)",
                         ["%moderate%", "%medium%"]),
            "low":      ("(rl.risk_label NOT ILIKE %s AND rl.risk_label NOT ILIKE %s "
                         "AND rl.risk_label NOT ILIKE %s)",
                         ["%high%", "%moderate%", "%medium%"]),
        }
        if self._risk_level in rl_filters:
            clause, vals = rl_filters[self._risk_level]
            clauses.append(clause)
            params.extend(vals)

        if self._college:
            clauses.append("dp.college = %s")
            params.append(self._college)
        if self._program:
            clauses.append("dp.program_name = %s")
            params.append(self._program)
        if self._gender:
            clauses.append("ds.sex_code = %s")
            params.append(self._gender)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def _term_join(self) -> str:
        return """
            JOIN public.dim_academic_term t
                 ON t.term_key = fsr.term_key
        """

    # dim_program / dim_risk_level / dim_student joins, added
    # unconditionally to queries that don't already have them inline, so
    # the WHERE clause built by _where() can reference dp.* / rl.* / ds.*
    # regardless of which filters are active. Queries 2-4 already join
    # dim_student inline (they need it for municipality/hs_type/income
    # themselves) — _DS_JOIN is only added to queries 1 and 6, which
    # otherwise have no reason to touch dim_student at all.
    _DP_JOIN = """
        LEFT JOIN public.dim_program dp
               ON dp.program_key = fsr.program_key
    """
    _RL_JOIN = """
        LEFT JOIN public.dim_risk_level rl
               ON rl.risk_level_id = fsr.risk_level_id
    """
    _DS_JOIN = """
        JOIN public.dim_student ds
             ON ds.student_key = fsr.student_key
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
                # Didn't previously join dim_student / dim_program /
                # dim_risk_level at all — added here (unused in SELECT,
                # only referenced by {where} when a filter is active) so
                # this chart respects the same Gender / Risk Level /
                # College / Program filters as the rest of the page.
                cur.execute(f"""
                    SELECT
                        COALESCE(fsr.primary_factor, 'Unknown') AS factor,
                        COUNT(*)                                 AS cnt
                    FROM  public.fact_student_academic_risk fsr
                    {self._term_join()}
                    {self._DS_JOIN}
                    {self._DP_JOIN}
                    {self._RL_JOIN}
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
                    {self._RL_JOIN}
                    {self._DP_JOIN}
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
                    {self._RL_JOIN}
                    {self._DP_JOIN}
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
                    {self._RL_JOIN}
                    {self._DP_JOIN}
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
                # Intentionally NOT extended with risk_level/college/
                # program filters — this query already ignores ay/sem
                # too, by design (always the single most-recent term).
                # Retrofitting the new filters here without also fixing
                # that pre-existing behavior would be inconsistent, so
                # it's left untouched — same scope boundary as the
                # ay/sem fix already noted below.
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
                    {self._DS_JOIN}
                    {self._RL_JOIN}
                    {self._DP_JOIN}
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

                # ── 7. Average HS GPA ─────────────────────────────────
                # high_school_gpa lives on the fact table itself (the
                # snapshot value used at prediction time), not
                # dim_student — same column already used by
                # StudentProfilePanel's SQL. AVG() skips NULLs natively,
                # so no extra null-filtering is needed beyond the shared
                # {where} filters.
                cur.execute(f"""
                    SELECT
                        AVG(fsr.high_school_gpa)::numeric(10,2) AS avg_gpa,
                        COUNT(fsr.high_school_gpa)              AS n
                    FROM  public.fact_student_academic_risk fsr
                    {self._term_join()}
                    {self._DS_JOIN}
                    {self._DP_JOIN}
                    {self._RL_JOIN}
                    {where}
                """, params)
                gpa_row = cur.fetchone()
                if gpa_row and gpa_row[0] is not None:
                    result["avg_hs_gpa"] = {
                        "average": float(gpa_row[0]),
                        "count":   int(gpa_row[1]),
                    }
                else:
                    result["avg_hs_gpa"] = {"average": None, "count": 0}

                # ── 8. SHS Strand vs risk ─────────────────────────────
                # Same shape/pattern as hs_type_risk above, grouped by
                # shs_strand instead — classifies which strand has the
                # most at-risk students. Ordered by combined high+
                # moderate count (highest risk first), same convention
                # already used for municipality_risk.
                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(ds.shs_strand, ''), 'Unknown') AS strand,
                        COUNT(*)                                       AS total,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%high%%'
                        )                                               AS high_risk,
                        COUNT(*) FILTER (
                            WHERE rl.risk_label ILIKE '%%moderate%%'
                               OR rl.risk_label ILIKE '%%medium%%'
                        )                                               AS moderate_risk
                    FROM  public.fact_student_academic_risk fsr
                    {self._term_join()}
                    JOIN  public.dim_student ds
                          ON ds.student_key = fsr.student_key
                    {self._RL_JOIN}
                    {self._DP_JOIN}
                    {where}
                    GROUP BY ds.shs_strand
                    ORDER BY (
                        COUNT(*) FILTER (WHERE rl.risk_label ILIKE '%%high%%') +
                        COUNT(*) FILTER (WHERE rl.risk_label ILIKE '%%moderate%%'
                                           OR  rl.risk_label ILIKE '%%medium%%')
                    ) DESC
                """, params)
                rows = cur.fetchall()
                result["shs_strand_risk"] = [
                    {
                        "strand":   r[0],
                        "total":    int(r[1]),
                        "high":     int(r[2]),
                        "moderate": int(r[3]),
                        "rate":     round((r[2] + r[3]) / max(r[1], 1) * 100, 1),
                    }
                    for r in rows
                ]

            self.finished.emit(result)

        except Exception as exc:
            self.error.emit(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# Term list + filter-dropdown loader
# ══════════════════════════════════════════════════════════════════════════════

class AnalyticsTermLoader(QThread):
    """
    Load distinct (academic_year, semester) pairs for the term filter bar,
    plus distinct college / program_name / sex_code values for the
    advanced filter dropdowns — all in one DB round-trip.

    FIX: queries dim_academic_term directly (with a LEFT JOIN to check
    existence) so the filter bar populates as soon as terms exist, even
    before any predictions have been run.  Falls back gracefully to an
    empty list if the table is empty.

    Signal shape: finished emits a dict
        {"terms": list[(ay, sem)], "colleges": list[str],
         "programs": list[str], "genders": list[str]}
    """
    finished = pyqtSignal(dict)
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

                # Advanced filter dropdown options
                cur.execute("""
                    SELECT DISTINCT college
                    FROM   public.dim_program
                    WHERE  college IS NOT NULL AND TRIM(college) <> ''
                    ORDER  BY college
                """)
                colleges = [r[0] for r in cur.fetchall()]

                cur.execute("""
                    SELECT DISTINCT program_name
                    FROM   public.dim_program
                    WHERE  program_name IS NOT NULL AND TRIM(program_name) <> ''
                    ORDER  BY program_name
                """)
                programs = [r[0] for r in cur.fetchall()]

                # Populated dynamically rather than assumed ("M"/"F" vs
                # "Male"/"Female" etc.) — see module docstring.
                cur.execute("""
                    SELECT DISTINCT sex_code
                    FROM   public.dim_student
                    WHERE  sex_code IS NOT NULL AND TRIM(sex_code) <> ''
                    ORDER  BY sex_code
                """)
                genders = [r[0] for r in cur.fetchall()]

                self.finished.emit({
                    "terms":    rows,
                    "colleges": colleges,
                    "programs": programs,
                    "genders":  genders,
                })
        except Exception as exc:
            self.error.emit(str(exc))