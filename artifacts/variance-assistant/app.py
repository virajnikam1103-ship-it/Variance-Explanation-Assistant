"""Thin Flask controllers for the Variance Explanation Assistant."""

import math
import os
from typing import Any

from flask import Flask, flash, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from ai_client import analyze_key_drivers
from database import (
    get_all_submissions,
    get_submission_by_id,
    init_db,
    save_submission,
)
from logic import compute_key_drivers


app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "variance-assistant-local-secret")
app.config["TEMPLATES_AUTO_RELOAD"] = True


def _submitted_rows() -> list[dict[str, str]]:
    """Read all structured input columns while preserving exactly what was typed."""
    categories = request.form.getlist("category")
    forecasts = request.form.getlist("forecast")
    actuals = request.form.getlist("actual")
    notes = request.form.getlist("notes")
    row_count = max(len(categories), len(forecasts), len(actuals), len(notes), 0)

    def at(values: list[str], index: int) -> str:
        return values[index] if index < len(values) else ""

    return [
        {
            "category": at(categories, index),
            "forecast": at(forecasts, index),
            "actual": at(actuals, index),
            "notes": at(notes, index),
        }
        for index in range(row_count)
    ]


def _validate_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate every row and return clean numeric rows plus field-level errors."""
    valid_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for index, row in enumerate(rows, start=1):
        category = row["category"].strip()
        forecast_text = row["forecast"].strip()
        actual_text = row["actual"].strip()
        row_errors: list[str] = []

        if not category:
            row_errors.append("Category is required.")

        forecast: float | None = None
        if not forecast_text:
            row_errors.append("Forecast is required.")
        else:
            try:
                forecast = float(forecast_text)
                if not math.isfinite(forecast):
                    raise ValueError
            except ValueError:
                row_errors.append("Forecast must be a valid number.")
            else:
                if forecast < 0:
                    row_errors.append("Forecast cannot be negative.")

        actual: float | None = None
        if not actual_text:
            row_errors.append("Actual is required.")
        else:
            try:
                actual = float(actual_text)
                if not math.isfinite(actual):
                    raise ValueError
            except ValueError:
                row_errors.append("Actual must be a valid number.")

        if row_errors:
            errors.extend(
                f"Row {index} · {message}" for message in row_errors
            )
        else:
            valid_rows.append(
                {
                    "category": category,
                    "forecast": forecast,
                    "actual": actual,
                    "notes": row["notes"].strip(),
                }
            )

    if not rows:
        errors.append("Add at least one variance row before analyzing.")
    return valid_rows, errors


@app.get("/")
def index():
    """Render the structured variance input form."""
    return render_template("index.html", rows=[{}, {}, {}], errors=[])


@app.get("/favicon.ico")
def favicon():
    """Serve the application icon without sending the browser through the error handler."""
    return app.send_static_file("favicon.svg")


@app.post("/analyze")
def analyze():
    """Validate input, compute drivers, call AI, save, and redirect to results."""
    submitted_rows = _submitted_rows()
    valid_rows, errors = _validate_rows(submitted_rows)
    if errors:
        return render_template(
            "index.html",
            rows=submitted_rows or [{}, {}, {}],
            errors=errors,
        ), 400

    computed_drivers = compute_key_drivers(valid_rows)
    ai_output, ai_error = analyze_key_drivers(computed_drivers)
    submission_id = save_submission(valid_rows, computed_drivers, ai_output)
    if ai_error:
        flash(ai_error, "warning")
    return redirect(url_for("results", submission_id=submission_id))


@app.get("/results/<int:submission_id>")
def results(submission_id: int):
    """Render one saved report as readable driver cards."""
    submission = get_submission_by_id(submission_id)
    if submission is None:
        return render_template(
            "error.html",
            title="Report not found",
            message="That variance report does not exist or may have been removed.",
        ), 404

    merged_drivers = []
    for computed, ai_result in zip(
        submission["computed_drivers"], submission["ai_output"]
    ):
        merged_drivers.append({**computed, **ai_result})
    return render_template("results.html", submission=submission, drivers=merged_drivers)


@app.get("/admin")
def admin():
    """Render report history and the low-confidence aggregate."""
    submissions = get_all_submissions()
    low_confidence_reports = sum(
        1 for submission in submissions if submission["low_confidence_flag"]
    )
    low_confidence_percentage = (
        (low_confidence_reports / len(submissions)) * 100 if submissions else 0
    )
    return render_template(
        "admin.html",
        submissions=submissions,
        low_confidence_percentage=low_confidence_percentage,
    )


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    """Log the real exception while showing a plain, safe user-facing page."""
    if isinstance(error, HTTPException):
        return error
    print(f"Unhandled application error: {error}")
    return render_template(
        "error.html",
        title="Something went wrong",
        message="Something went wrong. Please try again, and check the report history if you already submitted your data.",
    ), 500


init_db()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )