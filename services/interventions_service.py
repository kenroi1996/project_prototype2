"""
services/interventions_service.py
===================================
Backend workers and business logic for the AI Intervention Advisor.

Consumed by:
  ui/pages/interventions_page.py  — main page
  ui/pages/interventions_page.py  — log/detail dialogs

Nothing in this file imports from PyQt6.QtWidgets; only QtCore signals
are used so that workers can live on QThreads.
"""
from __future__ import annotations
import json
import re

from PyQt6.QtCore import QThread, pyqtSignal, QTimer

from services.data_store    import DataStore
from services.system_config import SystemConfig


def _safe_cleanup(worker: QThread) -> None:
    """
    Safely destroy a QThread worker regardless of which signal triggered
    the cleanup call.

    Rule: deleteLater() must never be called while run() is still executing.
    Qt guarantees that the `finished` signal fires AFTER run() returns, so
    connecting deleteLater to `finished` is safe.  The `error` signal is
    emitted FROM INSIDE run(), so the thread is still alive — calling
    deleteLater there causes "QThread: Destroyed while thread is still running".

    This helper uses wait() + a zero-delay QTimer so the event loop
    processes the thread-finished event before the C++ object is destroyed,
    making it safe to call from either signal.
    """
    if worker is None:
        return
    try:
        # Disconnect everything first so no slot fires after we start teardown
        worker.finished.disconnect()
    except RuntimeError:
        pass
    try:
        worker.error.disconnect()
    except RuntimeError:
        pass
    try:
        # Block up to 3 s for run() to return (near-instant for error path)
        if worker.isRunning():
            worker.quit()
            worker.wait(3000)
    except RuntimeError:
        pass
    # One event-loop tick so Qt can process the thread-finished event
    QTimer.singleShot(0, worker.deleteLater)


# ══════════════════════════════════════════════════════════════════════════════
# Prompts
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PER_STUDENT = """You are a JSON API. Output only raw JSON. No explanation, no prose, no markdown.
Given a student risk profile, return a JSON array of 3 intervention objects.
Each object has these exact keys: type, action, rationale, timeline, priority.
type values: Academic Support, Financial Aid, Counseling, Program Guidance, Peer Support
timeline values: Immediate, Within 2 weeks, This semester
Start your response with [ and end with ]"""

USER_PER_STUDENT = """Student: {name} | {program} | {college}
Risk: {score}% {risk_label} | Top factor: {factors}
Exam: {exam_score} | HS GPA: {hs_gpa}

["""

SYSTEM_COHORT = """You are a JSON API. Output only raw JSON. No explanation, no prose, no markdown.
Given cohort risk data, return a JSON array of 3 systemic issue objects.
Each object has these exact keys: issue, affected_count, description, recommended_action, priority.
Start your response with [ and end with ]"""

USER_COHORT = """Cohort: {term} | At-risk: {total} (High={high}, Moderate={moderate})
Top factors: {factors_summary}
By college: {college_summary}

["""


# ══════════════════════════════════════════════════════════════════════════════
# JSON utilities
# ══════════════════════════════════════════════════════════════════════════════

def parse_json_response(raw: str) -> list:
    """
    Robustly extract a JSON array from whatever the model returns.
    Handles: clean JSON, think-block prefixes, markdown fences,
    truncated responses, and trailing-comma quirks.
    """
    if not raw:
        return []
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
    except Exception:
        pass

    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    cleaned = re.sub(r"```(?:json)?", "", cleaned).replace("```", "").strip()
    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
    except Exception:
        pass

    # Walk bracket depth to find the first complete [...] block
    depth = 0
    start_idx = None
    for i, ch in enumerate(cleaned):
        if ch == "[":
            if start_idx is None:
                start_idx = i
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and start_idx is not None:
                candidate = cleaned[start_idx:i + 1]
                try:
                    result = json.loads(candidate)
                    if isinstance(result, list):
                        return result
                except Exception:
                    candidate = re.sub(r",\s*}", "}", candidate)
                    candidate = re.sub(r",\s*]", "]", candidate)
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, list):
                            return result
                    except Exception:
                        pass
                break

    # Last resort: recover as many complete objects as possible from a
    # truncated response by walking backwards through "}" positions.
    if start_idx is not None:
        search_end = len(cleaned)
        while search_end > start_idx:
            last_brace = cleaned.rfind("}", start_idx, search_end)
            if last_brace == -1:
                break
            truncated = cleaned[start_idx:last_brace + 1] + "]"
            try:
                result = json.loads(truncated)
                if isinstance(result, list) and result:
                    print(f"[interventions_service] Recovered "
                          f"{len(result)} items from truncated response")
                    return result
            except Exception:
                pass
            search_end = last_brace

    print(f"[interventions_service] Could not parse: {raw[:300]}")
    return []


def build_ollama_prompt(system: str, user: str) -> str:
    """Construct the chat-ML prefilled prompt for qwen3 / chatml models."""
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n["
    )


# ══════════════════════════════════════════════════════════════════════════════
# Ollama workers
# ══════════════════════════════════════════════════════════════════════════════

class OllamaWorker(QThread):
    """
    Single Ollama call — used for Ollama health-check and cohort summary.
    Emits the raw response string (already prefixed with "[").
    """
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, system: str, user: str):
        super().__init__()
        self._system = system
        self._user   = user

    def run(self):
        try:
            import requests
            url    = SystemConfig.ollama_url().rstrip("/")
            model  = SystemConfig.ollama_model()
            prompt = build_ollama_prompt(self._system, self._user)

            resp = requests.post(
                f"{url}/api/generate",
                json={
                    "model":  model,
                    "prompt": prompt,
                    "stream": True,
                    "raw":    True,
                    "options": {
                        "temperature": 0.2,
                        "top_p":       0.9,
                        "num_predict": 4096,
                        "num_ctx":     4096,
                        "stop":        ["<|im_end|>"],
                    },
                },
                stream=True,
                timeout=(10, None),
            )
            if resp.status_code != 200:
                self.error.emit(
                    f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
                return

            raw = ""
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    raw  += chunk.get("response", "")
                    if chunk.get("done"):
                        break
                except Exception:
                    continue

            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()
            if not raw.strip().startswith("["):
                raw = "[" + raw
            self.finished.emit(raw)

        except Exception as e:
            self.error.emit(str(e))


class BatchWorker(QThread):
    """
    Iterates all high-risk students sequentially, calling Ollama once per
    student — but only if no existing intervention record exists for that
    student in the same academic year/semester.

    Signals
    -------
    progress(done, total, current_name)
    one_done(student_dict, recommendations_list, skipped: bool)
        skipped=True  → existing record found, Ollama was NOT called,
                        recs are the previously saved recommendations.
        skipped=False → Ollama was called and produced new recommendations.
    finished()
    cancelled()
    error(message)
    """
    progress  = pyqtSignal(int, int, str)
    one_done  = pyqtSignal(dict, list, bool)   # student, recs, skipped
    finished  = pyqtSignal()
    cancelled = pyqtSignal()
    error     = pyqtSignal(str)

    def __init__(self, students: list[dict], academic_year: str, semester: int):
        super().__init__()
        self._students      = students
        self._academic_year = academic_year
        self._semester      = semester
        self._cancel        = False

    def cancel(self):
        self._cancel = True

    # ── Helpers ───────────────────────────────────────────────────────

    def _existing_recs(self, conn, student_id: str) -> list | None:
        """
        Return the existing recommendations list if this student already
        has a per_student intervention record for the current term,
        or None if no record exists.
        """
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT recommendations
                    FROM   public.interventions
                    WHERE  student_id    = %s
                      AND  academic_year = %s
                      AND  semester      = %s
                      AND  mode          = 'per_student'
                    ORDER  BY logged_at DESC
                    LIMIT  1
                """, (student_id, self._academic_year, self._semester))
                row = cur.fetchone()
            if not row:
                return None
            recs = row[0]
            if isinstance(recs, str):
                try:
                    recs = json.loads(recs)
                except Exception:
                    recs = []
            return recs if isinstance(recs, list) else []
        except Exception as exc:
            print(f"[BatchWorker] existing_recs check failed: {exc}")
            return None   # treat as no record → proceed with Ollama

    def run(self):
        import requests

        conn    = DataStore.get().db_conn
        url     = SystemConfig.ollama_url().rstrip("/")
        model   = SystemConfig.ollama_model()
        total   = len(self._students)
        api_url = f"{url}/api/generate"

        # A single Session reuses the keep-alive TCP connection to Ollama
        # for every student instead of opening a new connection each time.
        session = requests.Session()

        # Ollama generation options tuned for short structured JSON output.
        # The per-student prompt is ~150 tokens; 3 JSON objects are ~300 tokens.
        # Keeping context and prediction windows small cuts KV-cache allocation
        # time significantly on CPU-only hardware.
        _GEN_OPTIONS = {
            "temperature": 0.2,
            "top_p":       0.9,
            "num_predict": 512,   # was 2048 — JSON output never exceeds ~400 tokens
            "num_ctx":     512,   # was 2048 — prompt fits comfortably in 512 tokens
            "stop":        ["<|im_end|>"],
        }

        try:
            for idx, s in enumerate(self._students):
                if self._cancel:
                    self.cancelled.emit()
                    return

                name = s.get("full_name", "—")
                sid  = str(s.get("student_id", ""))
                self.progress.emit(idx, total, name)

                # ── Check for existing record ─────────────────────────
                if conn:
                    existing = self._existing_recs(conn, sid)
                    if existing is not None:
                        print(f"[BatchWorker] Skipping {name} — existing record found")
                        self.one_done.emit(s, existing, True)
                        continue

                # ── Call Ollama ───────────────────────────────────────
                score_raw = s.get("predicted_risk_score") or 0
                score     = round(float(score_raw) * 100, 1)
                exam      = s.get("entrance_exam_score")
                gpa       = s.get("high_school_gpa")
                factor    = s.get("primary_factor", "Not available")

                user_prompt = USER_PER_STUDENT.format(
                    name       = name,
                    program    = s.get("program", "—"),
                    college    = s.get("college", "—"),
                    score      = f"{score:.1f}",
                    risk_label = s.get("risk_label", "—"),
                    factors    = factor,
                    exam_score = f"{float(exam):.0f}" if exam else "N/A",
                    hs_gpa     = f"{float(gpa):.2f}"  if gpa  else "N/A",
                )
                prompt = build_ollama_prompt(SYSTEM_PER_STUDENT, user_prompt)

                try:
                    resp = session.post(
                        api_url,
                        json={
                            "model":   model,
                            "prompt":  prompt,
                            "stream":  True,
                            "raw":     True,
                            "options": _GEN_OPTIONS,
                        },
                        stream=True,
                        timeout=(10, None),
                    )
                    raw = ""
                    if resp.status_code == 200:
                        for line in resp.iter_lines():
                            if self._cancel:
                                self.cancelled.emit()
                                return
                            if not line:
                                continue
                            try:
                                chunk = json.loads(line)
                                raw  += chunk.get("response", "")
                                if chunk.get("done"):
                                    break
                            except Exception:
                                continue

                    raw = re.sub(r"<think>.*?</think>", "", raw,
                                 flags=re.DOTALL).strip()
                    raw = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()
                    if not raw.strip().startswith("["):
                        raw = "[" + raw
                    recs = parse_json_response(raw)

                except Exception as exc:
                    recs = []
                    print(f"[BatchWorker] {name}: {exc}")

                self.one_done.emit(s, recs, False)

        finally:
            session.close()

        self.progress.emit(total, total, "")
        self.finished.emit()


# ══════════════════════════════════════════════════════════════════════════════
# Database workers
# ══════════════════════════════════════════════════════════════════════════════

class SaveWorker(QThread):
    """Persist a single intervention record to public.interventions."""
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, record: dict):
        super().__init__()
        self._record = record

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            from services.auth_service import AuthService
            user         = AuthService.current_user() or {}
            counselor_id = user.get("user_id")
            r            = self._record
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.interventions
                        (student_id, counselor_id, academic_year, semester,
                         mode, risk_score, risk_label, risk_factors,
                         recommendations, logged_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb, NOW())
                """, (
                    r.get("student_id"),
                    counselor_id,
                    r.get("academic_year"),
                    r.get("semester"),
                    r.get("mode", "per_student"),
                    r.get("risk_score"),
                    r.get("risk_label"),
                    r.get("risk_factors"),
                    json.dumps(r.get("recommendations", [])),
                ))
            conn.commit()
            self.finished.emit()
        except Exception as e:
            try:
                DataStore.get().db_conn.rollback()
            except Exception:
                pass
            self.error.emit(str(e))


class TermLoader(QThread):
    """Load distinct (academic_year, semester) pairs that have predictions."""
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No DB connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT t.academic_year, t.semester
                    FROM   public.fact_student_academic_risk fsr
                    JOIN   public.dim_academic_term t
                           ON t.term_key = fsr.term_key
                    ORDER  BY t.academic_year DESC, t.semester DESC
                """)
                self.finished.emit(cur.fetchall())
        except Exception as e:
            self.error.emit(str(e))


class StudentLoader(QThread):
    """Load HIGH-risk students for a given term."""
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    _SQL = """
        SELECT
            ds.student_id,
            TRIM(COALESCE(ds.first_name,'') || ' ' ||
                 COALESCE(ds.last_name,''))             AS full_name,
            COALESCE(dp.program_name,'Unknown')         AS program,
            COALESCE(dp.college,'—')                    AS college,
            COALESCE(rl.risk_label,'High')              AS risk_label,
            fsr.predicted_risk_score,
            fsr.entrance_exam_score,
            fsr.high_school_gpa,
            fsr.primary_factor
        FROM  public.fact_student_academic_risk fsr
        JOIN  public.dim_academic_term t
              ON t.term_key       = fsr.term_key
        JOIN  public.dim_student ds
              ON ds.student_key   = fsr.student_key
        LEFT JOIN public.dim_program dp
              ON dp.program_key   = fsr.program_key
        LEFT JOIN public.dim_risk_level rl
              ON rl.risk_level_id = fsr.risk_level_id
        WHERE t.academic_year = %s AND t.semester = %s
          AND rl.risk_label ILIKE '%%high%%'
        ORDER BY fsr.predicted_risk_score DESC NULLS LAST
    """

    def __init__(self, ay: str, sem: int):
        super().__init__()
        self._ay, self._sem = ay, sem

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No DB connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(self._SQL, (self._ay, self._sem))
                cols = [d[0] for d in cur.description]
                self.finished.emit(
                    [dict(zip(cols, r)) for r in cur.fetchall()])
        except Exception as e:
            self.error.emit(str(e))


class InterventionRecordLoader(QThread):
    """Load all intervention records for a given term (used by export)."""
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, academic_year: str, semester: int):
        super().__init__()
        self._ay  = academic_year
        self._sem = semester

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT intervention_id, student_id, counselor_id,
                           academic_year, semester, mode,
                           risk_score, risk_label, risk_factors,
                           recommendations, notes, logged_at
                    FROM public.interventions
                    WHERE academic_year = %s AND semester = %s
                    ORDER BY logged_at ASC
                """, (self._ay, self._sem))
                cols = [d[0] for d in cur.description]
                self.finished.emit(
                    [dict(zip(cols, r)) for r in cur.fetchall()])
        except Exception as e:
            self.error.emit(str(e))


class InterventionReportWorker(QThread):
    """Generate intervention PDF off the main thread."""
    finished = pyqtSignal(str)   # saved file path
    error    = pyqtSignal(str)

    # ✅ Fix
    def __init__(self, records, term_label, academic_year, semester, save_path, config=None):
        super().__init__()
        self._records       = records
        self._term_label    = term_label
        self._academic_year = academic_year
        self._semester      = semester
        self._save_path     = save_path
        self._config        = config

    def run(self):
        try:
            from services.report_generator import InterventionReportGenerator
            gen = InterventionReportGenerator(
                self._records,
                self._term_label,
                self._academic_year,
                self._semester,
                config=self._config,
            )
            buf = gen.build_bytes()
            with open(self._save_path, "wb") as f:
                f.write(buf.getvalue())
            self.finished.emit(self._save_path)
        except Exception as e:
            self.error.emit(str(e))


class LogLoader(QThread):
    """Load intervention log rows with optional filters."""
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, filters: dict):
        super().__init__()
        self._filters = filters

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            f       = self._filters
            clauses = []
            params  = []

            if f.get("academic_year"):
                clauses.append("i.academic_year = %s")
                params.append(f["academic_year"])
            if f.get("semester"):
                clauses.append("i.semester = %s")
                params.append(int(f["semester"]))
            if f.get("mode"):
                clauses.append("i.mode = %s")
                params.append(f["mode"])
            if f.get("student_id"):
                clauses.append("i.student_id ILIKE %s")
                params.append(f"%{f['student_id']}%")
            if f.get("student_name"):
                clauses.append(
                    "(TRIM(COALESCE(ds.first_name,'') || ' ' || "
                    "COALESCE(ds.last_name,'')) ILIKE %s)")
                params.append(f"%{f['student_name']}%")
            if f.get("date_from"):
                clauses.append("i.logged_at >= %s")
                params.append(f["date_from"])
            if f.get("date_to"):
                clauses.append("i.logged_at <= %s")
                params.append(f["date_to"] + " 23:59:59")

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = f"""
                SELECT i.intervention_id, i.student_id,
                    TRIM(COALESCE(ds.first_name,'') || ' ' ||
                         COALESCE(ds.last_name,''))         AS student_name,
                    i.academic_year, i.semester, i.mode,
                    i.risk_score, i.risk_label, i.risk_factors,
                    jsonb_array_length(
                        COALESCE(i.recommendations,'[]'::jsonb)) AS rec_count,
                    i.logged_at
                FROM public.interventions i
                LEFT JOIN public.dim_student ds ON ds.student_id = i.student_id
                {where}
                ORDER BY i.logged_at DESC
            """
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                self.finished.emit(
                    [dict(zip(cols, r)) for r in cur.fetchall()])
        except Exception as e:
            self.error.emit(str(e))


class LogDeleter(QThread):
    """Delete a single intervention log record by ID."""
    finished = pyqtSignal(int)   # deleted intervention_id
    error    = pyqtSignal(str)

    def __init__(self, intervention_id: int):
        super().__init__()
        self._id = intervention_id

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM public.interventions "
                    "WHERE intervention_id = %s",
                    (self._id,))
            conn.commit()
            self.finished.emit(self._id)
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            self.error.emit(str(e))


class BatchLogDeleter(QThread):
    """
    Delete multiple intervention log records in a single query.
    Emits finished(deleted_ids) on success.
    """
    finished = pyqtSignal(list)   # list of deleted intervention_ids
    error    = pyqtSignal(str)

    def __init__(self, intervention_ids: list[int]):
        super().__init__()
        self._ids = list(intervention_ids)

    def run(self):
        conn = DataStore.get().db_conn
        if not conn:
            self.error.emit("No database connection.")
            return
        if not self._ids:
            self.finished.emit([])
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM public.interventions "
                    "WHERE intervention_id = ANY(%s) "
                    "RETURNING intervention_id",
                    (self._ids,)
                )
                deleted = [r[0] for r in cur.fetchall()]
            conn.commit()
            self.finished.emit(deleted)
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            self.error.emit(str(e))


# ══════════════════════════════════════════════════════════════════════════════
# Cohort summary helpers (pure Python, no Qt)
# ══════════════════════════════════════════════════════════════════════════════

def build_cohort_prompt(students: list[dict], ay: str, sem: int) -> str:
    """
    Build the Ollama prompt for a cohort-level systemic issues analysis.
    Returns the formatted user prompt string ready to pass to OllamaWorker.
    """
    total    = len(students)
    high     = sum(1 for s in students
                   if "high" in s.get("risk_label", "").lower())
    moderate = total - high

    fc: dict[str, int] = {}
    for s in students:
        f = s.get("primary_factor")
        if f:
            fc[f] = fc.get(f, 0) + 1
    factors_summary = ", ".join(
        f"{f}({c})" for f, c in
        sorted(fc.items(), key=lambda x: x[1], reverse=True)[:3]
    ) or "No factor data"

    cc: dict[str, int] = {}
    for s in students:
        c = s.get("college", "—")
        cc[c] = cc.get(c, 0) + 1
    college_summary = ", ".join(
        f"{col}({cnt})" for col, cnt in
        sorted(cc.items(), key=lambda x: x[1], reverse=True)[:3]
    ) or "—"

    sem_label = "1st Semester" if sem == 1 else "2nd Semester"
    term      = f"{ay} — {sem_label}"

    return USER_COHORT.format(
        term            = term,
        total           = total,
        high            = high,
        moderate        = moderate,
        factors_summary = factors_summary,
        college_summary = college_summary,
    )