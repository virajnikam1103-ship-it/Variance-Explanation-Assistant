"""Pure variance calculations and rule-based driver tagging."""

import re
from typing import Any


# This threshold is intentionally easy to tune as the team's definition of
# "unusually large" changes over time.
ANOMALY_THRESHOLD_PCT = 25


def _contains_keyword(notes: str, keywords: tuple[str, ...]) -> bool:
    """Check whether notes include one of the configured keywords."""
    note_text = notes.casefold()
    return any(re.search(rf"\b{re.escape(keyword)}\b", note_text) for keyword in keywords)


def _tag_driver(notes: str, variance_pct: float | None) -> str:
    """Apply the documented priority order to a driver's notes and percentage."""
    if _contains_keyword(notes, ("price", "rate")):
        return "price driver"
    if _contains_keyword(notes, ("delay", "pushed", "early")):
        return "timing driver"
    if not notes.strip() and variance_pct is not None and abs(variance_pct) >= ANOMALY_THRESHOLD_PCT:
        return "anomaly"
    return "volume driver"


def compute_key_drivers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute variances, rank the largest drivers, and apply rule-based tags.

    Takes validated rows containing category, forecast, actual, and notes.
    Returns up to five enriched driver dictionaries with no side effects or
    network calls, making this function safe to test independently.
    """
    enriched_rows: list[dict[str, Any]] = []

    for row in rows:
        forecast = float(row["forecast"])
        actual = float(row["actual"])
        variance = actual - forecast
        variance_pct = None if forecast == 0 else (variance / forecast) * 100
        notes = str(row.get("notes", "") or "")

        enriched_rows.append(
            {
                "category": str(row["category"]).strip(),
                "forecast": forecast,
                "actual": actual,
                "notes": notes,
                "variance": variance,
                "variance_pct": variance_pct,
                "rule_based_tag": _tag_driver(notes, variance_pct),
            }
        )

    ranked_rows = sorted(
        enriched_rows,
        key=lambda item: abs(item["variance"]),
        reverse=True,
    )
    driver_count = min(5, len(ranked_rows)) if ranked_rows else 0
    return ranked_rows[:driver_count]