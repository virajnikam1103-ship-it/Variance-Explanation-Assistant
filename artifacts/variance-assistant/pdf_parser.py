"""Lightweight table extraction for text-based variance PDFs."""

import re
from pathlib import Path
from typing import Any

import pymupdf as fitz


_NUMBER_PATTERN = re.compile(
    r"(?<![\w])(?:₹|\$|€|£)?\s*"
    r"(?:\(?-?\d[\d,]*(?:\.\d+)?\)?)(?![\w])"
)
_HEADER_WORDS = {
    "category": "Category",
    "categories": "Category",
    "item": "Category",
    "line item": "Category",
    "forecast": "Forecast",
    "budget": "Forecast",
    "plan": "Forecast",
    "actual": "Actual",
    "notes": "Notes",
    "note": "Notes",
    "comment": "Notes",
    "comments": "Notes",
}


def _clean_word(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _line_records(page: fitz.Page) -> list[dict[str, Any]]:
    words = page.get_text("words")
    grouped: dict[int, list[tuple[float, str]]] = {}
    for word in words:
        x0, y0, _x1, _y1, text, _block_no, _line_no, _word_no = word
        grouped.setdefault(round(y0), []).append((x0, text))
    records = []
    for y, line_words in sorted(grouped.items()):
        line_words.sort(key=lambda item: item[0])
        records.append(
            {
                "y": y,
                "x": line_words[0][0],
                "words": line_words,
                "text": " ".join(_clean_word(text) for _, text in line_words),
            }
        )
    return records


def _header_record(records: list[dict[str, Any]]) -> tuple[int, list[tuple[str, float]]]:
    for index, record in enumerate(records):
        matches: list[tuple[str, float]] = []
        for x, raw_word in record["words"]:
            normalized = re.sub(r"[^a-z ]", "", raw_word.casefold())
            if normalized in _HEADER_WORDS:
                matches.append((_HEADER_WORDS[normalized], x))
        names = {name for name, _ in matches}
        if "Forecast" in names and "Actual" in names:
            if "Category" not in names:
                matches.insert(0, ("Category", record["words"][0][0]))
            return index, matches
    return -1, []


def _fallback_columns(records: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """Use a stable four-column layout when the PDF has no recognizable header."""
    for record in records:
        if len(_NUMBER_PATTERN.findall(record["text"])) >= 2:
            return [
                ("Category", record["x"]),
                ("Forecast", record["x"] + 180),
                ("Actual", record["x"] + 300),
                ("Notes", record["x"] + 420),
            ]
    return [("Category", 0), ("Forecast", 180), ("Actual", 300), ("Notes", 420)]


def _parse_page(page: fitz.Page) -> tuple[list[dict[str, Any]], list[list[str]]]:
    records = _line_records(page)
    header_index, columns = _header_record(records)
    if not columns:
        columns = _fallback_columns(records)
        header_index = -1

    ordered_columns: list[tuple[str, float]] = []
    seen: set[str] = set()
    for name, x in sorted(columns, key=lambda item: item[1]):
        if name not in seen:
            ordered_columns.append((name, x))
            seen.add(name)
    column_names = [name for name, _ in ordered_columns]
    preview_rows: list[list[str]] = []

    anchors = [x for _, x in ordered_columns]
    for record in records[header_index + 1 :]:
        if not record["text"] or record["text"].casefold() in {"page", "page 1"}:
            continue
        cells = [""] * len(anchors)
        for x, word in record["words"]:
            column_index = min(range(len(anchors)), key=lambda index: abs(x - anchors[index]))
            cells[column_index] = f"{cells[column_index]} {word}".strip()
        if any(cells) and sum(bool(cell) for cell in cells) >= 2:
            preview_rows.append([_clean_word(cell) for cell in cells])

    columns_for_template = [
        {
            "index": index,
            "name": name,
            "label": f"{chr(65 + index)} — {name}",
        }
        for index, name in enumerate(column_names)
    ]
    return columns_for_template, preview_rows


def _page_index(sheet_name: str) -> int:
    match = re.fullmatch(r"Page (\d+)", sheet_name.strip(), re.IGNORECASE)
    if not match:
        raise ValueError("That PDF page could not be found.")
    return int(match.group(1)) - 1


def get_pdf_metadata(path: Path) -> dict[str, Any]:
    with fitz.open(path) as document:
        sheets = [f"Page {index}" for index in range(1, document.page_count + 1)]
        if not sheets:
            raise ValueError("The PDF does not contain any pages.")
        columns, preview_rows = _parse_page(document[0])
        return {"sheets": sheets, "columns": columns, "preview_rows": preview_rows}


def get_pdf_preview(
    path: Path, sheet_name: str, preview_limit: int = 5
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    with fitz.open(path) as document:
        index = _page_index(sheet_name)
        if index < 0 or index >= document.page_count:
            raise ValueError("That PDF page could not be found.")
        columns, rows = _parse_page(document[index])
        return columns, rows[:preview_limit]


def extract_pdf_variance_rows(
    path: Path,
    sheet_name: str,
    category_index: int,
    forecast_index: int,
    actual_index: int,
    notes_index: int | None = None,
) -> list[dict[str, str]]:
    columns, rows = get_pdf_preview(path, sheet_name, preview_limit=10000)
    if any(index < 0 or index >= len(columns) for index in (
        category_index,
        forecast_index,
        actual_index,
    )):
        raise ValueError("The selected columns are not available.")
    extracted: list[dict[str, str]] = []
    for row in rows:
        values = row + [""] * max(0, len(columns) - len(row))
        category = values[category_index]
        forecast = values[forecast_index]
        actual = values[actual_index]
        notes = values[notes_index] if notes_index is not None and notes_index < len(values) else ""
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