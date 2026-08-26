"""Thin Flask controllers for the Variance Explanation Assistant."""

import math
import os
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, flash, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.utils import secure_filename

from ai_client import analyze_key_drivers
from database import (
    get_all_submissions,
    get_submission_by_id,
    init_db,
    save_submission,
)
from excel_parser import extract_variance_rows, get_sheet_preview, get_workbook_metadata
from logic import compute_key_drivers


app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "variance-assistant-local-secret")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

UPLOAD_DIRECTORY = Path(__file__).with_name("uploads")
ALLOWED_WORKBOOK_EXTENSIONS = {".xlsx", ".xlsm"}


def _upload_path(upload_token: str) -> Path | None:
    """Resolve a temporary workbook token without accepting arbitrary paths."""
    if not upload_token or Path(upload_token).name != upload_token:
        return None
    candidate = UPLOAD_DIRECTORY / upload_token
    return candidate if candidate.is_file() else None


def _delete_upload(upload_token: str) -> None:
    """Remove a temporary workbook after its analysis has been processed."""
    path = _upload_path(upload_token)
    if path:
        path.unlink(missing_ok=True)


def _upload_template_context(
    *,
    is_mapping: bool = False,
    upload_error: str | None = None,
    mapping_error: str | None = None,
    **values: Any,
) -> dict[str, Any]:
    """Build consistent defaults for the two states of the upload screen."""
    return {
        "is_mapping": is_mapping,
        "upload_error": upload_error,
        "mapping_error": mapping_error,
        "mapping_errors": values.get("mapping_errors", []),
        "upload_token": values.get("upload_token", ""),
        "workbook_name": values.get("workbook_name", ""),
        "sheets": values.get("sheets", []),
        "selected_sheet": values.get("selected_sheet", ""),
        "columns": values.get("columns", []),
        "preview_rows": values.get("preview_rows", []),
        "reasoning": values.get("reasoning", ""),
        "selected_category": values.get("selected_category", ""),
        "selected_forecast": values.get("selected_forecast", ""),
        "selected_actual": values.get("selected_actual", ""),
        "selected_notes": values.get("selected_notes", ""),
    }


def _render_upload(**context: Any):
    """Render the upload screen with explicit, safe template defaults."""
    return render_template("upload.html", **_upload_template_context(**context))


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


@app.get("/upload")
def upload():
    """Render the consolidated workbook upload screen."""
    return _render_upload()


@app.post("/upload")
def upload_workbook():
    """Save a workbook temporarily and render its sheet and column mapper."""
    workbook_file = request.files.get("workbook")
    reasoning = request.form.get("reasoning", "").strip()
    if workbook_file is None or not workbook_file.filename:
        return _render_upload(upload_error="Choose an Excel workbook to continue."), 400

    suffix = Path(workbook_file.filename).suffix.casefold()
    if suffix not in ALLOWED_WORKBOOK_EXTENSIONS:
        return _render_upload(
            upload_error="Only .xlsx and .xlsm workbooks are supported."
        ), 400

    UPLOAD_DIRECTORY.mkdir(exist_ok=True)
    upload_token = f"{uuid.uuid4().hex}{suffix}"
    upload_path = UPLOAD_DIRECTORY / secure_filename(upload_token)
    workbook_file.save(upload_path)

    try:
        metadata = get_workbook_metadata(upload_path)
        if not metadata["sheets"]:
            raise ValueError("The workbook does not contain a worksheet.")
    except Exception as error:  # noqa: BLE001 - malformed files need a friendly response.
        upload_path.unlink(missing_ok=True)
        print(f"Workbook metadata failed: {error}")
        return _render_upload(
            upload_error="We could not read that workbook. Check that it is a valid Excel file."
        ), 400

    return _render_upload(
        is_mapping=True,
        upload_token=upload_token,
        workbook_name=secure_filename(workbook_file.filename),
        sheets=metadata["sheets"],
        selected_sheet=metadata["sheets"][0],
        columns=metadata["columns"],
        preview_rows=metadata["preview_rows"],
        reasoning=reasoning,
    )


@app.post("/upload/sheet")
def load_workbook_sheet():
    """Refresh the mapper preview when the analyst chooses another worksheet."""
    upload_token = request.form.get("upload_token", "")
    upload_path = _upload_path(upload_token)
    workbook_name = request.form.get("workbook_name", "Uploaded workbook")
    reasoning = request.form.get("reasoning", "").strip()
    selected_sheet = request.form.get("selected_sheet", "")
    if upload_path is None:
        return _render_upload(
            upload_error="This workbook upload has expired. Please attach it again."
        ), 400

    try:
        metadata = get_workbook_metadata(upload_path)
        columns, preview_rows = get_sheet_preview(upload_path, selected_sheet)
        return _render_upload(
            is_mapping=True,
            upload_token=upload_token,
            workbook_name=workbook_name,
            sheets=metadata["sheets"],
            selected_sheet=selected_sheet,
            columns=columns,
            preview_rows=preview_rows,
            reasoning=reasoning,
        )
    except Exception as error:  # noqa: BLE001 - invalid sheet selections need a friendly response.
        print(f"Workbook sheet preview failed: {error}")
        return _render_upload(
            is_mapping=True,
            mapping_error="We could not load that worksheet. Choose another sheet and try again.",
            upload_token=upload_token,
            workbook_name=workbook_name,
            sheets=get_workbook_metadata(upload_path)["sheets"],
            reasoning=reasoning,
        ), 400


@app.post("/upload/analyze")
def analyze_workbook():
    """Map an uploaded workbook into variance rows, analyze it, and save the report."""
    upload_token = request.form.get("upload_token", "")
    upload_path = _upload_path(upload_token)
    workbook_name = request.form.get("workbook_name", "Uploaded workbook")
    reasoning = request.form.get("reasoning", "").strip()
    selected_sheet = request.form.get("selected_sheet", "")
    try:
        category_index = int(request.form.get("category_col", ""))
        forecast_index = int(request.form.get("forecast_col", ""))
        actual_index = int(request.form.get("actual_col", ""))
        notes_value = request.form.get("notes_col", "")
        notes_index = int(notes_value) if notes_value else None
    except ValueError:
        return _render_upload(
            is_mapping=True,
            mapping_error="Choose a category, forecast, and actual column before analyzing.",
            upload_token=upload_token,
            workbook_name=workbook_name,
            reasoning=reasoning,
            selected_sheet=selected_sheet,
        ), 400

    if upload_path is None:
        return _render_upload(
            upload_error="This workbook upload has expired. Please attach it again."
        ), 400

    delete_after_request = False
    try:
        if len({category_index, forecast_index, actual_index}) < 3:
            return _render_upload(
                is_mapping=True,
                mapping_error="Category, forecast, and actual must be different columns.",
                upload_token=upload_token,
                workbook_name=workbook_name,
                reasoning=reasoning,
                selected_sheet=selected_sheet,
            ), 400
        columns, preview_rows = get_sheet_preview(upload_path, selected_sheet)
        mapped_rows = extract_variance_rows(
            upload_path,
            selected_sheet,
            category_index,
            forecast_index,
            actual_index,
            notes_index,
        )
        valid_rows, errors = _validate_rows(mapped_rows)
        if errors:
            return _render_upload(
                is_mapping=True,
                mapping_error="Please correct the workbook data before analyzing.",
                mapping_errors=errors,
                upload_token=upload_token,
                workbook_name=workbook_name,
                sheets=get_workbook_metadata(upload_path)["sheets"],
                selected_sheet=selected_sheet,
                columns=columns,
                preview_rows=preview_rows,
                reasoning=reasoning,
                selected_category=str(category_index),
                selected_forecast=str(forecast_index),
                selected_actual=str(actual_index),
                selected_notes=str(notes_index) if notes_index is not None else "",
            ), 400

        computed_drivers = compute_key_drivers(valid_rows)
        ai_output, ai_error = analyze_key_drivers(computed_drivers, reasoning)
        submission_id = save_submission(
            valid_rows,
            computed_drivers,
            ai_output,
            analysis_context=reasoning,
        )
        if ai_error:
            flash(ai_error, "warning")
        delete_after_request = True
        return redirect(url_for("results", submission_id=submission_id))
    except Exception as error:  # noqa: BLE001 - file parsing must fail safely.
        print(f"Workbook analysis failed: {error}")
        return _render_upload(
            is_mapping=True,
            mapping_error="We could not process that workbook selection. Check the sheet and columns, then try again.",
            upload_token=upload_token,
            workbook_name=workbook_name,
            reasoning=reasoning,
            selected_sheet=selected_sheet,
        ), 400
    finally:
        if delete_after_request:
            _delete_upload(upload_token)


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

    max_percentage = max(
        (
            abs(driver["variance_pct"])
            for driver in submission["computed_drivers"]
            if driver.get("variance_pct") is not None
        ),
        default=1,
    )
    merged_drivers = []
    for computed, ai_result in zip(
        submission["computed_drivers"], submission["ai_output"]
    ):
        merged_driver = {**computed, **ai_result}
        merged_driver["bar_width"] = (
            min(100, abs(computed["variance_pct"]) / max_percentage * 100)
            if computed.get("variance_pct") is not None
            else 0
        )
        merged_drivers.append(merged_driver)
    return render_template(
        "results.html",
        submission=submission,
        drivers=merged_drivers,
        analysis_context=submission.get("analysis_context", ""),
    )


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


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(error: RequestEntityTooLarge):
    """Explain the workbook size limit without Flask's default error page."""
    if request.path.startswith("/upload"):
        return _render_upload(
            upload_error="Workbook uploads must be 10 MB or smaller."
        ), 413
    return render_template(
        "error.html",
        title="File too large",
        message="The submitted file is too large.",
    ), 413


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