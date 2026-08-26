"""Helpers for treating CSV exports as mapper-friendly tabular sources."""

import csv
from pathlib import Path
from typing import Any


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _column_letter(index: int) -> str:
    letters = ""
    current = index + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _read_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = [
            [_display_value(value) for value in row]
            for row in csv.reader(source)
        ]
    while rows and not any(rows[0]):
        rows.pop(0)
    if not rows:
        raise ValueError("The sheet is empty.")
    width = max(len(row) for row in rows)
    return [row + [""] * (width - len(row)) for row in rows]


def get_csv_metadata(path: Path) -> dict[str, Any]:
    """Return one logical sheet and its first rows for the mapping screen."""
    columns, preview_rows = get_csv_preview(path)
    return {
        "sheets": ["Google Sheet"],
        "columns": columns,
        "preview_rows": preview_rows,
    }


def get_csv_preview(
    path: Path, sheet_name: str = "Google Sheet", preview_limit: int = 5
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    """Read CSV headers and a small preview using the Excel parser contract."""
    if sheet_name != "Google Sheet":
        raise ValueError("That sheet could not be found.")
    rows = _read_rows(path)
    header = rows[0]
    columns = [
        {
            "index": index,
            "name": value or f"Column {index + 1}",
            "label": f"{_column_letter(index)} — {value or f'Column {index + 1}'}",
        }
        for index, value in enumerate(header)
    ]
    return columns, rows[1 : preview_limit + 1]


def extract_csv_variance_rows(
    path: Path,
    sheet_name: str,
    category_index: int,
    forecast_index: int,
    actual_index: int,
    notes_index: int | None = None,
) -> list[dict[str, str]]:
    """Extract mapped CSV rows into the shared variance input shape."""
    columns, _ = get_csv_preview(path, sheet_name, preview_limit=0)
    rows = _read_rows(path)[1:]
    if any(index < 0 or index >= len(columns) for index in (
        category_index,
        forecast_index,
        actual_index,
    )):
        raise ValueError("The selected columns are not available.")

    extracted: list[dict[str, str]] = []
    for row in rows:
        category = row[category_index]
        forecast = row[forecast_index]
        actual = row[actual_index]
        notes = row[notes_index] if notes_index is not None and notes_index < len(row) else ""
        if not any((category, forecast, actual, notes)):
            continue
        extracted.append(
            {
                "category": category,
                "forecast": forecast,
                "actual": actual,
                "notes": notes,
            }
        )
    return extracted