"""Data preprocessing / cleaning engine — pure logic, no UI."""

import csv as _csv_mod
import pandas as pd
import numpy as np
from typing import Optional
from sklearn.preprocessing import LabelEncoder, StandardScaler, MinMaxScaler


def _is_numeric(value: str) -> bool:
    try:
        float(value.strip())
        return True
    except (ValueError, AttributeError):
        return False


class CleaningEngine:
    """Applies a sequence of cleaning steps to tabular data."""

    @staticmethod
    def apply(headers: list, rows: list, steps: list) -> tuple:
        h = list(headers)
        r = [list(row) for row in rows]

        for step in steps:
            op = step["op"]

            if op == "fill_missing_mean":
                col = step["params"].get("col")
                if col in h:
                    idx = h.index(col)
                    nums = []
                    for row in r:
                        try:
                            if row[idx].strip():
                                nums.append(float(row[idx]))
                        except ValueError:
                            pass
                    fill = str(round(sum(nums) / len(nums), 4)) if nums else "0"
                    for row in r:
                        if not row[idx].strip():
                            row[idx] = fill

            elif op == "fill_missing_median":
                col = step["params"].get("col")
                if col in h:
                    idx = h.index(col)
                    nums = sorted(
                        float(row[idx]) for row in r
                        if row[idx].strip() and _is_numeric(row[idx])
                    )
                    if nums:
                        mid = len(nums) // 2
                        fill = str(nums[mid] if len(nums) % 2 else
                                   (nums[mid - 1] + nums[mid]) / 2)
                    else:
                        fill = "0"
                    for row in r:
                        if not row[idx].strip():
                            row[idx] = fill

            elif op == "fill_missing_mode":
                col = step["params"].get("col")
                if col in h:
                    idx = h.index(col)
                    counts = {}
                    for row in r:
                        v = row[idx].strip()
                        if v:
                            counts[v] = counts.get(v, 0) + 1
                    fill = max(counts, key=counts.get) if counts else ""
                    for row in r:
                        if not row[idx].strip():
                            row[idx] = fill

            elif op == "fill_missing_value":
                col = step["params"].get("col")
                value = step["params"].get("value", "")
                if col in h:
                    idx = h.index(col)
                    for row in r:
                        if not row[idx].strip():
                            row[idx] = value

            elif op == "remove_duplicates":
                seen = set()
                cleaned = []
                for row in r:
                    key = tuple(row)
                    if key not in seen:
                        seen.add(key)
                        cleaned.append(row)
                r = cleaned

            elif op == "remove_empty_rows":
                r = [row for row in r if any(c.strip() for c in row)]

            elif op == "normalize":
                col = step["params"].get("col")
                if col in h:
                    idx = h.index(col)
                    nums = [float(row[idx]) for row in r if _is_numeric(row[idx])]
                    if nums:
                        mn, mx = min(nums), max(nums)
                        rng = mx - mn if mx != mn else 1
                        for row in r:
                            if _is_numeric(row[idx]):
                                row[idx] = str(round((float(row[idx]) - mn) / rng, 4))

            elif op == "encode_categorical":
                col = step["params"].get("col")
                if col in h:
                    idx = h.index(col)
                    labels = {}
                    counter = 0
                    for row in r:
                        v = row[idx].strip()
                        if v not in labels:
                            labels[v] = counter
                            counter += 1
                        row[idx] = str(labels[v])

            elif op == "remove_outliers":
                col = step["params"].get("col")
                if col in h:
                    idx = h.index(col)
                    nums = [float(row[idx]) for row in r if _is_numeric(row[idx])]
                    if nums:
                        mean = sum(nums) / len(nums)
                        std = (sum((x - mean) ** 2 for x in nums) / len(nums)) ** 0.5
                        r = [
                            row for row in r
                            if not _is_numeric(row[idx]) or
                            abs(float(row[idx]) - mean) <= 3 * std
                        ]

            elif op == "drop_column":
                col = step["params"].get("col")
                if col in h:
                    idx = h.index(col)
                    h = [c for i, c in enumerate(h) if i != idx]
                    r = [[c for i, c in enumerate(row) if i != idx] for row in r]

            # ── NEW: Filter by values (stackable) ──────────────────────
            elif op == "filter_by_values":
                col = step["params"].get("col")
                values = set(step["params"].get("values", []))
                if col in h and values:
                    idx = h.index(col)
                    r = [
                        row for row in r
                        if idx < len(row) and row[idx].strip() in values
                    ]

        return h, r


def compute_issues(headers: list, rows: list) -> dict:
    missing_rows = [i for i, row in enumerate(rows) if any(not cell.strip() for cell in row)]
    missing = len(missing_rows)

    seen = {}
    dupes_rows = []
    for i, row in enumerate(rows):
        key = tuple(row)
        if key in seen:
            dupes_rows.append(i)
        else:
            seen[key] = i
    dupes = len(dupes_rows)

    empty_rows = [i for i, row in enumerate(rows) if not any(c.strip() for c in row)]

    invalid_cols = {}
    for i, col in enumerate(headers):
        col_vals = [row[i] for row in rows if row[i].strip()]
        non_num = sum(1 for v in col_vals if not _is_numeric(v))
        if col_vals and non_num < len(col_vals) * 0.5:
            if non_num:
                invalid_cols[col] = non_num

    return {
        "missing": missing,
        "missing_rows": missing_rows,
        "duplicates": dupes,
        "dupes_rows": dupes_rows,
        "empty_rows": len(empty_rows),
        "empty_rows_indices": empty_rows,
        "invalid": invalid_cols,
    }


def compute_quality_score(headers: list, rows: list) -> int:
    issues = compute_issues(headers, rows)
    total_cells = len(rows) * len(headers) if rows and headers else 1
    bad = issues["missing"] + issues["duplicates"]
    return max(0, min(100, int(100 * (1 - bad / max(total_cells, 1)))))


def get_unique_column_values(rows: list, headers: list, col_name: str) -> list:
    if col_name not in headers:
        return []

    col_idx = headers.index(col_name)
    unique_vals = set()

    for row in rows:
        if col_idx < len(row):
            val = row[col_idx].strip()
            if val:
                unique_vals.add(val)

    try:
        return sorted(unique_vals, key=lambda x: float(x))
    except (ValueError, TypeError):
        return sorted(unique_vals)


def filter_rows_by_values(rows: list, headers: list, col_name: str, values: set) -> list:
    if col_name not in headers:
        return []

    col_idx = headers.index(col_name)
    matching = []

    for i, row in enumerate(rows):
        if col_idx < len(row) and row[col_idx].strip() in values:
            matching.append(i)

    return matching


def save_dataset(path: str, headers: list, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = _csv_mod.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


# =============================================================================
# PANDAS-BASED PIPELINE (for automated ML pipeline)
# =============================================================================

class DataPipeline:
    """High-level pipeline for Excel → Cleaned Dataset → ML-ready data."""

    def __init__(self, df: pd.DataFrame):
        self._original_df = df.copy()
        self.df = df.copy()
        self._encoders: dict[str, LabelEncoder] = {}
        self._scaler: Optional[StandardScaler | MinMaxScaler] = None
        self._target_column: Optional[str] = None
        self._feature_columns: list[str] = []
        self._categorical_columns: list[str] = []
        self._numerical_columns: list[str] = []

    def validate_columns(self, required: Optional[list[str]] = None) -> dict:
        result = {"valid": True, "missing": [], "empty": [], "info": ""}

        if self.df.empty:
            result["valid"] = False
            result["info"] = "DataFrame is empty"
            return result

        if required:
            missing = [c for c in required if c not in self.df.columns]
            if missing:
                result["valid"] = False
                result["missing"] = missing
                result["info"] = f"Missing required columns: {missing}"
                return result

        empty_cols = [c for c in self.df.columns if self.df[c].isna().all()]
        if empty_cols:
            result["empty"] = empty_cols
            result["info"] = f"Empty columns detected: {empty_cols}"

        result["info"] = f"{len(self.df)} rows, {len(self.df.columns)} columns"
        return result

    def remove_duplicates(self, subset: Optional[list[str]] = None) -> "DataPipeline":
        before = len(self.df)
        self.df = self.df.drop_duplicates(subset=subset).reset_index(drop=True)
        return self

    def fill_missing(self, strategy: str = "auto", columns: Optional[list[str]] = None) -> "DataPipeline":
        target_cols = columns if columns else self.df.columns.tolist()

        if strategy == "drop":
            self.df = self.df.dropna(subset=target_cols).reset_index(drop=True)
            return self

        for col in target_cols:
            if col not in self.df.columns:
                continue

            if self.df[col].isna().all():
                continue

            if strategy == "auto":
                if pd.api.types.is_numeric_dtype(self.df[col]):
                    self.df[col] = self.df[col].fillna(self.df[col].mean())
                else:
                    mode_val = self.df[col].mode()
                    fill_val = mode_val[0] if not mode_val.empty else "Unknown"
                    self.df[col] = self.df[col].fillna(fill_val)
            elif strategy == "mean":
                if pd.api.types.is_numeric_dtype(self.df[col]):
                    self.df[col] = self.df[col].fillna(self.df[col].mean())
            elif strategy == "median":
                if pd.api.types.is_numeric_dtype(self.df[col]):
                    self.df[col] = self.df[col].fillna(self.df[col].median())
            elif strategy == "mode":
                mode_val = self.df[col].mode()
                fill_val = mode_val[0] if not mode_val.empty else "Unknown"
                self.df[col] = self.df[col].fillna(fill_val)

        return self

    def encode_categorical(self, columns: Optional[list[str]] = None, drop_first: bool = False) -> "DataPipeline":
        # Build candidate list — always exclude the target column
        if columns:
            target_cols = [c for c in columns if c != self._target_column]
        else:
            target_cols = [
                c for c in self.df.select_dtypes(include=["object", "category"]).columns
                if c != self._target_column
            ]

        for col in target_cols:
            if col not in self.df.columns:
                continue

            unique_count = self.df[col].nunique(dropna=True)
            if unique_count == 0:
                continue

            if unique_count > 10 or not drop_first:
                le = LabelEncoder()
                filled = self.df[col].fillna("__MISSING__").astype(str)
                self.df[col] = le.fit_transform(filled)
                self._encoders[col] = le
            else:
                dummies = pd.get_dummies(self.df[col], prefix=col, drop_first=drop_first)
                self.df = pd.concat([self.df.drop(columns=[col]), dummies], axis=1)

        self._categorical_columns = target_cols
        return self

    def scale_numerical(self, method: str = "standard", columns: Optional[list[str]] = None) -> "DataPipeline":
        # Build candidate list — always exclude the target column
        if columns:
            target_cols = [c for c in columns if c != self._target_column]
        else:
            target_cols = [
                c for c in self.df.select_dtypes(include=[np.number]).columns
                if c != self._target_column
            ]

        target_cols = [c for c in target_cols if c in self.df.columns]

        if not target_cols:
            return self

        self._numerical_columns = target_cols

        if method == "standard":
            self._scaler = StandardScaler()
        else:
            self._scaler = MinMaxScaler()

        self.df[target_cols] = self._scaler.fit_transform(self.df[target_cols])
        return self

    def generate_risk_labels(self,
                           rules: Optional[dict] = None,
                           target_col: str = "risk_label",
                           based_on: Optional[str] = None) -> "DataPipeline":
        if rules:
            self.df[target_col] = self.df.apply(
                lambda row: self._apply_risk_rules(row, rules), axis=1
            )
        elif based_on and based_on in self.df.columns:
            self.df[target_col] = pd.qcut(
                self.df[based_on].rank(method='first'),
                q=3,
                labels=["low", "medium", "high"]
            )
        else:
            numeric_cols = self.df.select_dtypes(include=[np.number]).columns
            if len(numeric_cols) > 0:
                self.df[target_col] = pd.qcut(
                    self.df[numeric_cols[0]].rank(method='first'),
                    q=3,
                    labels=["low", "medium", "high"]
                )
            else:
                self.df[target_col] = "unknown"

        self._target_column = target_col
        return self

    @staticmethod
    def _apply_risk_rules(row: pd.Series, rules: dict) -> str:
        for label, condition in rules.items():
            try:
                if condition(row):
                    return label
            except Exception:
                continue
        return "unknown"

    def prepare_features(self,
                         target_col: Optional[str] = None,
                         drop_cols: Optional[list[str]] = None) -> tuple[np.ndarray, np.ndarray, list[str]]:
        if target_col:
            self._target_column = target_col

        df = self.df.copy()

        if drop_cols:
            df = df.drop(columns=[c for c in drop_cols if c in df.columns])

        if self._target_column and self._target_column in df.columns:
            y = df[self._target_column].values
            X_df = df.drop(columns=[self._target_column])
        else:
            y = np.zeros(len(df))
            X_df = df

        X_df = X_df.select_dtypes(include=[np.number])
        self._feature_columns = X_df.columns.tolist()

        return X_df.values, y, self._feature_columns

    def get_summary(self) -> dict:
        return {
            "original_rows": len(self._original_df),
            "current_rows": len(self.df),
            "columns": list(self.df.columns),
            "numeric_columns": list(self.df.select_dtypes(include=[np.number]).columns),
            "categorical_columns": list(self.df.select_dtypes(include=["object", "category"]).columns),
            "missing_values": int(self.df.isna().sum().sum()),
            "memory_mb": round(self.df.memory_usage(deep=True).sum() / 1024 / 1024, 2),
        }

    def to_csv(self, path: str) -> None:
        self.df.to_csv(path, index=False)

    def to_records(self) -> tuple[list, list]:
        headers = list(self.df.columns)
        rows = self.df.astype(str).fillna("").values.tolist()
        return headers, rows