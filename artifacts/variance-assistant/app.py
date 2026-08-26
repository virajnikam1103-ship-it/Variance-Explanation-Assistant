"""Thin Flask controllers for the Variance Explanation Assistant."""

import math
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
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
from google_sheets import (
    download_google_sheet,
    download_private_google_sheet,
    get_private_sheet_metadata,
)
from logic import compute_key_drivers
from pdf_parser import extract_pdf_variance_rows, get_pdf_metadata, get_pdf_preview
from presentation import (
    build_report_presentation,
    format_indian_date,
    format_inr,
    project_summary,
)
from tabular_parser import extract_csv_variance_rows, get_csv_metadata, get_csv_preview


app = Flask(__name__)
app.secret_key = os.getenv("SESSION_SECRET", "variance-assistant-local-secret")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

UPLOAD_DIRECTORY = Path(__file__).with_name("uploads")
ALLOWED_UPLOAD_EXTENSIONS = {".xlsx", ".xlsm", ".pdf"}
UPLOAD_EXPIRY = timedelta(minutes=30)
UPLOAD_CLEANUP_INTERVAL_SECONDS = 5 * 60


def _upload_path(upload_token: str) -> Path | None:
    """Resolve a temporary source token without accepting arbitrary paths."""
    if not upload_token or Path(upload_token).name != upload_token:
        return None
    candidate = UPLOAD_DIRECTORY / upload_token
    if (
        candidate.is_file()
        and datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
        < datetime.now(timezone.utc) - UPLOAD_EXPIRY
    ):
        candidate.unlink(missing_ok=True)
        return None
    return candidate if candidate.is_file() else None


def _delete_upload(upload_token: str) -> None:
    """Remove a temporary source after its analysis has been processed."""
    path = _upload_path(upload_token)
    if path:
        path.unlink(missing_ok=True)


def _cleanup_expired_uploads() -> None:
    """Clear abandoned temporary source files before starting a new import."""
    if not UPLOAD_DIRECTORY.is_dir():
        return
    expired_before = datetime.now(timezone.utc) - UPLOAD_EXPIRY
    for candidate in UPLOAD_DIRECTORY.iterdir():
        if candidate.is_file() and datetime.fromtimestamp(
            candidate.stat().st_mtime, tz=timezone.utc
        ) < expired_before:
            candidate.unlink(missing_ok=True)


def _start_upload_cleanup_worker() -> None:
    """Periodically remove abandoned temporary imports without user activity."""
    def cleanup_loop() -> None:
        while True:
            threading.Event().wait(UPLOAD_CLEANUP_INTERVAL_SECONDS)
            try:
                _cleanup_expired_uploads()
            except OSError as error:
                print(f"Temporary upload cleanup failed: {error}")

    threading.Thread(
        target=cleanup_loop,
        name="temporary-upload-cleanup",
        daemon=True,
    ).start()


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
        "source_kind": values.get("source_kind", "workbook"),
        "source_label": values.get("source_label", "Workbook"),
        "sheets": values.get("sheets", []),
        "selected_sheet": values.get("selected_sheet", ""),
        "columns": values.get("columns", []),
        "preview_rows": values.get("preview_rows", []),
        "reasoning": values.get("reasoning", ""),
        "selected_category": values.get("selected_category", ""),
        "selected_forecast": values.get("selected_forecast", ""),
        "selected_actual": values.get("selected_actual", ""),
        "selected_notes": values.get("selected_notes", ""),
        "is_private_sheet_selection": values.get("is_private_sheet_selection", False),
        "private_sheet_url": values.get("private_sheet_url", ""),
        "private_workbook_title": values.get("private_workbook_title", ""),
        "private_sheets": values.get("private_sheets", []),
        "private_reasoning": values.get("private_reasoning", ""),
    }


def _render_upload(**context: Any):
    """Render the upload screen with explicit, safe template defaults."""
    return render_template("upload.html", **_upload_template_context(**context))


@app.before_request
def clean_expired_uploads_before_import() -> None:
    """Sweep stale imports whenever an upload-flow request reaches the app."""
    if request.path.startswith("/upload"):
        _cleanup_expired_uploads()


def _source_metadata(path: Path, source_kind: str) -> dict[str, Any]:
    if source_kind == "pdf":
        return get_pdf_metadata(path)
    if source_kind in {"google_sheet", "private_google_sheet"}:
        return get_csv_metadata(path)
    return get_workbook_metadata(path)


def _source_preview(
    path: Path, source_kind: str, selected_sheet: str
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    if source_kind == "pdf":
        return get_pdf_preview(path, selected_sheet)
    if source_kind in {"google_sheet", "private_google_sheet"}:
        return get_csv_preview(path, selected_sheet)
    return get_sheet_preview(path, selected_sheet)


def _source_rows(
    path: Path,
    source_kind: str,
    selected_sheet: str,
    category_index: int,
    forecast_index: int,
    actual_index: int,
    notes_index: int | None,
) -> list[dict[str, str]]:
    if source_kind == "pdf":
        return extract_pdf_variance_rows(
            path,
            selected_sheet,
            category_index,
            forecast_index,
            actual_index,
            notes_index,
        )
    if source_kind in {"google_sheet", "private_google_sheet"}:
        return extract_csv_variance_rows(
            path,
            selected_sheet,
            category_index,
            forecast_index,
            actual_index,
            notes_index,
        )
    return extract_variance_rows(
        path,
        selected_sheet,
        category_index,
        forecast_index,
        actual_index,
        notes_index,
    )


@app.template_filter("inr")
def inr_filter(value: Any) -> str:
    """Expose INR formatting to templates using Indian digit grouping."""
    return format_inr(value)


@app.template_filter("inr_signed")
def signed_inr_filter(value: Any) -> str:
    """Expose signed INR formatting to templates."""
    return format_inr(value, signed=True)


@app.template_filter("india_date")
def india_date_filter(value: str) -> str:
    """Expose DD/MM/YYYY formatting to templates."""
    return format_indian_date(value)


@app.context_processor
def sidebar_projects() -> dict[str, Any]:
    """Provide real recent saved reports to the shared sidebar."""
    submissions = get_all_submissions()[:6]
    return {"sidebar_projects": [project_summary(item) for item in submissions]}


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


def _parse_amount(value: str) -> float:
    """Accept plain or Indian-formatted numeric amounts from imported sources."""
    normalized = (
        value.strip()
        .replace("₹", "")
        .replace(",", "")
        .replace(" ", "")
        .replace("\u2212", "-")
    )
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = f"-{normalized[1:-1]}"
    return float(normalized)


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
                forecast = _parse_amount(forecast_text)
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
                actual = _parse_amount(actual_text)
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
    """Render the product landing page and its two workflow choices."""
    return render_template("index.html")


@app.get("/analysis/new")
def new_analysis():
    """Render the existing structured manual-entry workflow."""
    return render_template("analysis_form.html", rows=[{}, {}, {}], errors=[])


@app.get("/upload")
@app.get("/analysis/upload")
def upload():
    """Render the consolidated source import screen."""
    _cleanup_expired_uploads()
    return _render_upload()


@app.post("/upload")
@app.post("/analysis/upload")
def upload_workbook():
    """Save an Excel/PDF source temporarily and render its mapper."""
    _cleanup_expired_uploads()
    workbook_file = request.files.get("workbook")
    reasoning = request.form.get("reasoning", "").strip()
    if workbook_file is None or not workbook_file.filename:
        return _render_upload(upload_error="Choose an Excel workbook or PDF to continue."), 400

    suffix = Path(workbook_file.filename).suffix.casefold()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        return _render_upload(
            upload_error="Use an .xlsx, .xlsm, or .pdf file."
        ), 400

    UPLOAD_DIRECTORY.mkdir(exist_ok=True)
    upload_token = f"{uuid.uuid4().hex}{suffix}"
    upload_path = UPLOAD_DIRECTORY / secure_filename(upload_token)
    workbook_file.save(upload_path)
    source_kind = "pdf" if suffix == ".pdf" else "workbook"
    source_label = "PDF" if source_kind == "pdf" else "Excel workbook"

    try:
        metadata = _source_metadata(upload_path, source_kind)
        if not metadata["sheets"]:
            raise ValueError("The source does not contain a readable table.")
    except Exception as error:  # noqa: BLE001 - malformed files need a friendly response.
        upload_path.unlink(missing_ok=True)
        print(f"{source_label} metadata failed: {error}")
        return _render_upload(
            upload_error=f"We could not read that {source_label.lower()}. "
            "Check that it contains a text-based table with Category, Forecast, and Actual data."
        ), 400

    return _render_upload(
        is_mapping=True,
        upload_token=upload_token,
        workbook_name=secure_filename(workbook_file.filename),
        source_kind=source_kind,
        source_label=source_label,
        sheets=metadata["sheets"],
        selected_sheet=metadata["sheets"][0],
        columns=metadata["columns"],
        preview_rows=metadata["preview_rows"],
        reasoning=reasoning,
    )


@app.post("/upload/google-sheet")
def upload_google_sheet():
    """Download a public Google Sheet as CSV and render the shared mapper."""
    _cleanup_expired_uploads()
    sheet_url = request.form.get("google_sheet_url", "").strip()
    reasoning = request.form.get("reasoning", "").strip()
    if not sheet_url:
        return _render_upload(
            upload_error="Paste a Google Sheets link to continue."
        ), 400

    UPLOAD_DIRECTORY.mkdir(exist_ok=True)
    upload_token = f"{uuid.uuid4().hex}.csv"
    upload_path = UPLOAD_DIRECTORY / upload_token
    try:
        download_google_sheet(sheet_url, upload_path)
        metadata = get_csv_metadata(upload_path)
    except Exception as error:  # noqa: BLE001 - remote source failures need friendly copy.
        upload_path.unlink(missing_ok=True)
        print(f"Google Sheet import failed: {error}")
        return _render_upload(
            upload_error=str(error)
        ), 400

    return _render_upload(
        is_mapping=True,
        upload_token=upload_token,
        workbook_name="Google Sheet",
        source_kind="google_sheet",
        source_label="Google Sheet",
        sheets=metadata["sheets"],
        selected_sheet=metadata["sheets"][0],
        columns=metadata["columns"],
        preview_rows=metadata["preview_rows"],
        reasoning=reasoning,
    )


@app.post("/upload/private-google-sheet")
def choose_private_google_sheet():
    """Load accessible private worksheet choices through the managed OAuth proxy."""
    _cleanup_expired_uploads()
    sheet_url = request.form.get("private_google_sheet_url", "").strip()
    reasoning = request.form.get("reasoning", "").strip()
    if not sheet_url:
        return _render_upload(
            upload_error="Paste a Google Sheets link to continue."
        ), 400

    try:
        metadata = get_private_sheet_metadata(sheet_url)
    except ValueError as error:
        print(f"Private Google Sheet metadata failed: {error}")
        return _render_upload(upload_error=str(error)), 400

    return _render_upload(
        is_private_sheet_selection=True,
        private_sheet_url=sheet_url,
        private_workbook_title=metadata["title"],
        private_sheets=metadata["sheets"],
        private_reasoning=reasoning,
    )


@app.post("/upload/private-google-sheet/worksheet")
def upload_private_google_sheet():
    """Export the selected private worksheet temporarily for the common mapper."""
    _cleanup_expired_uploads()
    sheet_url = request.form.get("private_sheet_url", "").strip()
    worksheet_title = request.form.get("worksheet_title", "").strip()
    reasoning = request.form.get("reasoning", "").strip()
    if not sheet_url or not worksheet_title:
        return _render_upload(
            upload_error="Choose a private Google Sheet and worksheet to continue."
        ), 400

    UPLOAD_DIRECTORY.mkdir(exist_ok=True)
    upload_token = f"{uuid.uuid4().hex}.csv"
    upload_path = UPLOAD_DIRECTORY / upload_token
    try:
        workbook_name = download_private_google_sheet(
            sheet_url, worksheet_title, upload_path
        )
        metadata = get_csv_metadata(upload_path)
    except ValueError as error:
        upload_path.unlink(missing_ok=True)
        print(f"Private Google Sheet import failed: {error}")
        return _render_upload(upload_error=str(error)), 400

    return _render_upload(
        is_mapping=True,
        upload_token=upload_token,
        workbook_name=f"{workbook_name} — {worksheet_title}",
        source_kind="private_google_sheet",
        source_label="Private Google Sheet",
        sheets=metadata["sheets"],
        selected_sheet=metadata["sheets"][0],
        columns=metadata["columns"],
        preview_rows=metadata["preview_rows"],
        reasoning=reasoning,
    )


@app.post("/upload/cancel")
def cancel_upload():
    """Remove the temporary import source when an analyst exits the mapper."""
    _delete_upload(request.form.get("upload_token", ""))
    return redirect(url_for("upload"))


@app.post("/upload/sheet")
def load_workbook_sheet():
    """Refresh the mapper preview for an uploaded source."""
    upload_token = request.form.get("upload_token", "")
    upload_path = _upload_path(upload_token)
    workbook_name = request.form.get("workbook_name", "Uploaded workbook")
    source_kind = request.form.get("source_kind", "workbook")
    source_label = request.form.get("source_label", "Workbook")
    reasoning = request.form.get("reasoning", "").strip()
    selected_sheet = request.form.get("selected_sheet", "")
    if upload_path is None:
        return _render_upload(
            upload_error="This source upload has expired. Please attach it again."
        ), 400

    try:
        metadata = _source_metadata(upload_path, source_kind)
        columns, preview_rows = _source_preview(
            upload_path, source_kind, selected_sheet
        )
        return _render_upload(
            is_mapping=True,
            upload_token=upload_token,
            workbook_name=workbook_name,
            source_kind=source_kind,
            source_label=source_label,
            sheets=metadata["sheets"],
            selected_sheet=selected_sheet,
            columns=columns,
            preview_rows=preview_rows,
            reasoning=reasoning,
        )
    except Exception as error:  # noqa: BLE001 - invalid sheet selections need a friendly response.
        print(f"{source_label} preview failed: {error}")
        return _render_upload(
            is_mapping=True,
            mapping_error=f"We could not load that {source_label.lower()} section. "
            "Choose another one and try again.",
            upload_token=upload_token,
            workbook_name=workbook_name,
            source_kind=source_kind,
            source_label=source_label,
            sheets=_source_metadata(upload_path, source_kind)["sheets"],
            reasoning=reasoning,
        ), 400


@app.post("/upload/analyze")
def analyze_workbook():
    """Map an imported source into variance rows, analyze it, and save the report."""
    upload_token = request.form.get("upload_token", "")
    upload_path = _upload_path(upload_token)
    workbook_name = request.form.get("workbook_name", "Uploaded workbook")
    source_kind = request.form.get("source_kind", "workbook")
    source_label = request.form.get("source_label", "Workbook")
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
            source_kind=source_kind,
            source_label=source_label,
            reasoning=reasoning,
            selected_sheet=selected_sheet,
        ), 400

    if upload_path is None:
        return _render_upload(
            upload_error="This source upload has expired. Please attach it again."
        ), 400

    delete_after_request = False
    try:
        if len({category_index, forecast_index, actual_index}) < 3:
            return _render_upload(
                is_mapping=True,
                mapping_error="Category, forecast, and actual must be different columns.",
                upload_token=upload_token,
                workbook_name=workbook_name,
                source_kind=source_kind,
                source_label=source_label,
                reasoning=reasoning,
                selected_sheet=selected_sheet,
            ), 400
        columns, preview_rows = _source_preview(
            upload_path,
            source_kind,
            selected_sheet,
        )
        mapped_rows = _source_rows(
            upload_path,
            source_kind,
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
                source_kind=source_kind,
                source_label=source_label,
                sheets=_source_metadata(upload_path, source_kind)["sheets"],
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
        print(f"{source_label} analysis failed: {error}")
        return _render_upload(
            is_mapping=True,
            mapping_error=f"We could not process that {source_label.lower()} selection. "
            "Check the source and columns, then try again.",
            upload_token=upload_token,
            workbook_name=workbook_name,
            source_kind=source_kind,
            source_label=source_label,
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
            "analysis_form.html",
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
@app.get("/reports/<int:submission_id>")
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
    for rank, (computed, ai_result) in enumerate(zip(
        submission["computed_drivers"], submission["ai_output"]
    ), start=1):
        merged_driver = {**computed, **ai_result}
        merged_driver["bar_width"] = (
            min(100, abs(computed["variance_pct"]) / max_percentage * 100)
            if computed.get("variance_pct") is not None
            else 0
        )
        merged_driver["rank"] = rank
        merged_driver["evidence_note"] = computed.get("notes") or (
            "No supporting note was supplied for this line item."
        )
        confidence = float(merged_driver.get("confidence", 0))
        merged_driver["confidence_label"] = (
            "High Evidence" if confidence >= 80 else "Medium Evidence"
            if confidence >= 60 else "Insufficient Evidence"
        )
        merged_driver["plan_direction"] = (
            "Above plan" if computed["variance"] > 0 else "Below plan"
            if computed["variance"] < 0 else "On plan"
        )
        merged_drivers.append(merged_driver)
    presentation = build_report_presentation(submission)
    return render_template(
        "results.html",
        submission=submission,
        drivers=merged_drivers,
        analysis_context=submission.get("analysis_context", ""),
        report=presentation,
    )


@app.get("/admin")
@app.get("/history")
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


@app.get("/projects")
def projects():
    """Render the database-backed variance project list."""
    submissions = get_all_submissions()
    return render_template(
        "projects.html",
        projects=[project_summary(item) for item in submissions],
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
_start_upload_cleanup_worker()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )