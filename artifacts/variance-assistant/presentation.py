"""Presentation helpers for finance-friendly report views."""

from datetime import datetime
from typing import Any


def _as_number(value: Any) -> float:
    """Convert a stored value to a float while keeping malformed data harmless."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _indian_grouping(value: int) -> str:
    """Format an integer using Indian digit grouping without locale dependencies."""
    digits = str(abs(value))
    if len(digits) <= 3:
        return digits
    tail = digits[-3:]
    head = digits[:-3]
    groups: list[str] = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(groups + [tail])


def format_inr(value: Any, signed: bool = False) -> str:
    """Format an amount as INR with Indian grouping and two decimal places."""
    amount = _as_number(value)
    prefix = ""
    if signed and amount > 0:
        prefix = "+"
    elif amount < 0:
        prefix = "−"
    absolute = abs(amount)
    whole = int(absolute)
    decimals = int(round((absolute - whole) * 100))
    if decimals == 100:
        whole += 1
        decimals = 0
    return f"{prefix}₹{_indian_grouping(whole)}.{decimals:02d}"


def format_indian_date(value: str) -> str:
    """Render stored ISO timestamps with a DD/MM/YYYY date convention."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return value
    return parsed.strftime("%d/%m/%Y")


def financial_year(value: str) -> str:
    """Return the Indian April-to-March financial year covering a timestamp."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return "Financial Year"
    start_year = parsed.year if parsed.month >= 4 else parsed.year - 1
    return f"FY {start_year}–{str(start_year + 1)[-2:]}"


def reporting_period(value: str) -> str:
    """Return a quarter label using the Indian April-to-March financial year."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return "Reporting Period"
    quarter = ((parsed.month - 4) % 12) // 3 + 1
    return f"Q{quarter} {financial_year(value)}"


def report_title(submission: dict[str, Any]) -> str:
    """Create a meaningful saved-report title from the actual leading driver."""
    drivers = submission.get("computed_drivers", [])
    if drivers and drivers[0].get("category"):
        return f"{drivers[0]['category']} Review"
    return f"Variance Review #{submission.get('id', '')}"


def project_summary(submission: dict[str, Any]) -> dict[str, Any]:
    """Build a concise, data-backed saved-report item for navigation lists."""
    return {
        "id": submission["id"],
        "title": report_title(submission),
        "date": format_indian_date(submission["timestamp"]),
        "financial_year": financial_year(submission["timestamp"]),
        "period": reporting_period(submission["timestamp"]),
        "driver_count": len(submission.get("computed_drivers", [])),
        "needs_review": bool(submission.get("low_confidence_flag")),
    }


def build_report_presentation(submission: dict[str, Any]) -> dict[str, Any]:
    """Create visual report data from stored calculations without changing logic."""
    raw_rows = submission.get("raw_input", [])
    line_items: list[dict[str, Any]] = []
    for row in raw_rows:
        forecast = _as_number(row.get("forecast"))
        actual = _as_number(row.get("actual"))
        variance = actual - forecast
        variance_pct = None if forecast == 0 else (variance / forecast) * 100
        line_items.append(
            {
                "category": str(row.get("category", "Unlabelled item")),
                "forecast": forecast,
                "actual": actual,
                "variance": variance,
                "variance_pct": variance_pct,
                "notes": str(row.get("notes", "") or ""),
            }
        )

    total_forecast = sum(item["forecast"] for item in line_items)
    total_actual = sum(item["actual"] for item in line_items)
    net_variance = total_actual - total_forecast
    material_count = sum(
        1
        for item in line_items
        if item["variance_pct"] is not None and abs(item["variance_pct"]) >= 10
    )
    largest_driver = max(line_items, key=lambda item: abs(item["variance"]), default=None)

    return {
        "title": report_title(submission),
        "date": format_indian_date(submission["timestamp"]),
        "financial_year": financial_year(submission["timestamp"]),
        "period": reporting_period(submission["timestamp"]),
        "summary": {
            "total_forecast": total_forecast,
            "total_actual": total_actual,
            "net_variance": net_variance,
            "material_count": material_count,
            "largest_driver": largest_driver,
        },
        "line_items": sorted(
            line_items, key=lambda item: abs(item["variance"]), reverse=True
        ),
    }