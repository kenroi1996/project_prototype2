"""Excel reading utilities using pandas."""

import pandas as pd
from pathlib import Path


def read_excel_file(path: str | Path, **kwargs) -> pd.DataFrame:
    """Read an Excel file into a pandas DataFrame."""
    path = Path(path)
    
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    
    try:
        if path.suffix.lower() in ['.xlsx', '.xls']:
            df = pd.read_excel(path, **kwargs)
        else:
            df = pd.read_csv(path, **kwargs)
    except Exception as e:
        raise ValueError(f"Failed to read file: {e}")
    
    if df.empty:
        raise ValueError("File is empty (no rows)")
    
    df.columns = df.columns.astype(str).str.strip()
    return df


def dataframe_to_rows(df: pd.DataFrame) -> tuple[list, list]:
    """Convert DataFrame to headers + rows for UI."""
    headers = list(df.columns)
    rows = df.astype(str).fillna("").values.tolist()
    return headers, rows


def rows_to_dataframe(headers: list, rows: list) -> pd.DataFrame:
    """Convert headers + rows back to DataFrame."""
    return pd.DataFrame(rows, columns=headers)