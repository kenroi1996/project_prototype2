from __future__ import annotations


# =====================================
# STUDENT ID COLUMN ALIASES
# Each portal may name the student ID differently.
# Listed in priority order.
# =====================================

STUDENT_ID_ALIASES: dict[str, list[str]] = {
    "mis":       ["ID_NO", "KEYID", "SYSTEMCODE", "student_id", "STUDENT_ID"],
    "sao":       ["STUDENT_ID", "student_id", "ID_NO", "KEYID"],
    "guidance":  ["student_id", "systemcode", "STUDENT_ID", "ID_NO"],
    "registrar": ["student_id", "STUDENT_ID", "ID_NO", "KEYID"],
}

# =====================================
# UNIFIED FEATURE MAP
# Maps unified column name → list of
# possible source column names across portals.
# First match wins.
# =====================================

UNIFIED_FEATURE_MAP: dict[str, list[str]] = {
    "Student_ID":                ["ID_NO", "KEYID", "SYSTEMCODE", "student_id", "STUDENT_ID"],
        # ── Name columns — MIS portal typically provides these ────────────
    "First_Name":               ["FIRSTNAME", "FIRST_NAME", "first_name", "fname", "FNAME", "GIVEN_NAME", "given_name"],
    "Last_Name":                 ["LASTNAME", "LAST_NAME", "last_name", "lname","LNAME", "SURNAME", "surname"],
    "Full_Name":                 ["FULL_NAME", "full_name", "STUDENT_NAME","student_name", "NAME", "name"],
    "Program":                   ["PROGRAM", "program_code", "PROGRAM_CODE"],
    "College":                   ["COLLEGE", "college"],
    "SecCode":                   ["SECCODE", "SEC_CODE"],
    "Year":                      ["YEAR", "year"],
    "Sex_code":                  ["SEX_CODE", "GENDER", "gender", "SEX"],
    "Home_Address":              ["HOME_ADDRESS", "home_address"],
    "Municipality":              ["MUNICIPALITY", "municipality", "HOME_MUNICIPALITY", "home_municipality"],
    "Civil_Status":              ["CIVIL_STATUS", "civil_status"],
    "Entrance_Exam_Score":       ["entrance_exam_score", "ENTRANCE_EXAM_SCORE"],
    "Family_Income":             ["family_income_bracket", "FAMILY_INCOME", "family_income"],
    "Parent_Highest_Education":  ["parent_highest_education", "PARENT_HIGHEST_EDUCATION"],
    "HS_GPA":                    ["hs_gpa", "HS_GPA"],
    "Year_Graduated":            ["year_graduated", "YEAR_GRADUATED"],
    "SHS_Strand":                ["shs_strand", "SHS_STRAND"],
    "HS_Type":                   ["hs_type", "HS_TYPE"],
    "Graduation_Honors":         ["graduation_honors", "GRADUATION_HONORS"],
    "HS_School":                 ["hs_school", "HS_SCHOOL", "HS_SCHOOLNAME"],
    "Year_Enrolled":             ["year_enrolled", "YEAR_ENROLLED"],
    "Scholarship_Applicant":     ["SCHOLARSHIP_APPLICANT", "scholarship_applicant"],
    "Scholarship_Type":          ["SCHOLARSHIP_TYPE", "scholarship_type"],
    "Birthdate":                 ["BIRTHDATE", "birthdate"],
    "Final_Avg_GRD":             ["FINAL_AVG_GRD", "final_avg_grd", "FINAL_GRADE"],
    "Religion":                  ["RELIGION", "religion"],
}

UNIFIED_COLUMNS = list(UNIFIED_FEATURE_MAP.keys())


# =====================================
# MERGE ENGINE
# =====================================

class MergeEngine:
    """
    Merges the four portal datasets into one unified dataset
    via left join on Student ID (MIS is the master source).

    Usage
    -----
        from ui.services.data_store import DataStore
        from ui.services.merge_engine import MergeEngine

        store   = DataStore.get()
        result  = MergeEngine.merge(store.portals)

        result.headers        # list[str]
        result.rows           # list[list[str]]
        result.report         # MergeReport
    """

    @classmethod
    def merge(cls, portals: dict) -> "MergeResult":
        """
        Parameters
        ----------
        portals : dict
            {"mis": {"headers": [...], "rows": [...]}, ...}

        Returns
        -------
        MergeResult
        """
        errors = []

        # ── Validate all portals present ─────────────────────────────
        for key in ("mis", "sao", "guidance", "registrar"):
            if portals.get(key) is None:
                errors.append(f"Portal '{key}' has no data.")

        if errors:
            return MergeResult([], [], MergeReport(errors=errors))

        # ── Find ID columns ──────────────────────────────────────────
        id_cols: dict[str, str] = {}
        for key in ("mis", "sao", "guidance", "registrar"):
            col = cls._find_id_column(portals[key]["headers"], key)
            if col is None:
                errors.append(
                    f"Cannot find student ID column in '{key}' portal. "
                    f"Headers: {portals[key]['headers']}"
                )
            else:
                id_cols[key] = col

        if errors:
            return MergeResult([], [], MergeReport(errors=errors))

        # ── Normalize IDs and build lookup dicts ─────────────────────
        lookups: dict[str, dict] = {}
        for key in ("mis", "sao", "guidance", "registrar"):
            lookups[key] = cls._build_lookup(
                portals[key]["headers"],
                portals[key]["rows"],
                id_cols[key],
            )

        # ── Master ID set from MIS ────────────────────────────────────
        master_ids = sorted(lookups["mis"].keys())

        # ── Merge rows ───────────────────────────────────────────────
        unified_rows        = []
        unmatched: dict     = {k: 0 for k in ("sao", "guidance", "registrar")}
        matched_count       = 0

        for sid in master_ids:
            merged: dict[str, str] = {}

            # Start with MIS (master)
            merged.update(lookups["mis"].get(sid, {}))

            # Fill from other portals
            for key in ("sao", "guidance", "registrar"):
                record = lookups[key].get(sid)
                if record:
                    # Only fill empty fields — don't overwrite MIS data
                    for col, val in record.items():
                        if col not in merged or not merged[col].strip():
                            merged[col] = val
                    matched_count += 1
                else:
                    unmatched[key] += 1

            # Build unified row in UNIFIED_COLUMNS order
            row = cls._map_to_unified(merged)
            row[0] = sid   # ensure Student_ID is always the normalized ID
            unified_rows.append(row)

        report = MergeReport(
            total_master    = len(master_ids),
            total_merged    = len(unified_rows),
            unmatched       = unmatched,
            id_columns      = id_cols,
            errors          = [],
        )

        return MergeResult(UNIFIED_COLUMNS, unified_rows, report)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_id_column(headers: list[str], portal_key: str) -> str | None:
        """Find the student ID column by checking aliases first, then fuzzy."""
        aliases = STUDENT_ID_ALIASES.get(portal_key, [])
        for alias in aliases:
            if alias in headers:
                return alias
        # Fuzzy fallback
        for h in headers:
            if "id" in h.lower() and "student" in h.lower():
                return h
        for h in headers:
            if h.lower() in ("id_no", "student_id", "keyid", "systemcode"):
                return h
        return None

    @staticmethod
    def _normalize_id(raw_id: str) -> str:
        """
        Normalize student IDs so '2024-001' and '2024001' match.
        Strips whitespace, lowercases, removes dashes.
        """
        return raw_id.strip().lower().replace("-", "").replace(" ", "")

    @classmethod
    def _build_lookup(
        cls,
        headers: list[str],
        rows: list[list[str]],
        id_col: str,
    ) -> dict[str, dict[str, str]]:
        """Build {normalized_id: {col: value}} dict."""
        idx    = headers.index(id_col)
        result = {}
        for row in rows:
            if idx >= len(row):
                continue
            raw_id = row[idx]
            if not raw_id.strip():
                continue
            norm_id          = cls._normalize_id(raw_id)
            result[norm_id]  = dict(zip(headers, row))
        return result

    @staticmethod
    def _map_to_unified(merged: dict[str, str]) -> list[str]:
        """Map a merged record dict to a row in UNIFIED_COLUMNS order."""
        row = []
        for unified_col, aliases in UNIFIED_FEATURE_MAP.items():
            value = ""
            for alias in aliases:
                if alias in merged and merged[alias].strip():
                    value = merged[alias].strip()
                    break
            row.append(value)
        return row

    # ------------------------------------------------------------------
    # Column inspection utility
    # ------------------------------------------------------------------

    @staticmethod
    def detect_id_columns(portals: dict) -> dict[str, str | None]:
        """
        Return detected ID column for each portal without merging.
        Useful for previewing before merge.
        """
        result = {}
        for key in ("mis", "sao", "guidance", "registrar"):
            data = portals.get(key)
            if data:
                result[key] = MergeEngine._find_id_column(data["headers"], key)
            else:
                result[key] = None
        return result


# =====================================
# RESULT + REPORT DATACLASSES
# =====================================

class MergeReport:
    def __init__(
        self,
        total_master:  int       = 0,
        total_merged:  int       = 0,
        unmatched:     dict      = None,
        id_columns:    dict      = None,
        errors:        list[str] = None,
    ):
        self.total_master  = total_master
        self.total_merged  = total_merged
        self.unmatched     = unmatched or {}
        self.id_columns    = id_columns or {}
        self.errors        = errors or []

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    @property
    def coverage_pct(self) -> float:
        if self.total_master == 0:
            return 0.0
        matched = self.total_master - max(self.unmatched.values(), default=0)
        return round(matched / self.total_master * 100, 1)

    def summary(self) -> str:
        lines = [
            f"Merge Report",
            f"  Master (MIS) rows : {self.total_master:,}",
            f"  Merged rows       : {self.total_merged:,}",
            f"  Unified columns   : {len(UNIFIED_COLUMNS)}",
        ]
        for key, count in self.unmatched.items():
            lines.append(f"  Unmatched {key:10s}: {count:,}")
        if self.errors:
            lines.append("  Errors:")
            for e in self.errors:
                lines.append(f"    - {e}")
        return "\n".join(lines)


class MergeResult:
    def __init__(
        self,
        headers: list[str],
        rows:    list[list[str]],
        report:  MergeReport,
    ):
        self.headers = headers
        self.rows    = rows
        self.report  = report

    @property
    def success(self) -> bool:
        return self.report.success and len(self.rows) > 0