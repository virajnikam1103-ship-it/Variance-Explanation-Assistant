"""Small SQLite persistence layer for submitted variance reports."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATABASE_PATH = Path(__file__).with_name("variance.db")


def _connection() -> sqlite3.Connection:
    """Open a SQLite connection configured to return named row values."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    """Create the report table if it does not exist yet."""
    with _connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                raw_input TEXT NOT NULL,
                computed_drivers TEXT NOT NULL,
                ai_output TEXT NOT NULL,
                avg_confidence REAL NOT NULL,
                low_confidence_flag INTEGER NOT NULL,
                analysis_context TEXT NOT NULL DEFAULT ''
            )
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(submissions)").fetchall()
        }
        if "analysis_context" not in columns:
            connection.execute(
                "ALTER TABLE submissions ADD COLUMN analysis_context TEXT NOT NULL DEFAULT ''"
            )


def save_submission(
    raw_input: list[dict[str, Any]],
    computed_drivers: list[dict[str, Any]],
    ai_output: list[dict[str, Any]],
    analysis_context: str = "",
) -> int:
    """Save one analysis and return its new database id.

    The optional analyst context is stored separately so workbook reasoning
    remains visible evidence without being mixed into the raw row values.
    """
    confidences = [float(item["confidence"]) for item in ai_output]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0
    low_confidence_flag = any(
        bool(item.get("low_confidence")) for item in ai_output
    )

    with _connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO submissions (
                timestamp,
                raw_input,
                computed_drivers,
                ai_output,
                avg_confidence,
                low_confidence_flag
                ,
                analysis_context
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                json.dumps(raw_input),
                json.dumps(computed_drivers),
                json.dumps(ai_output),
                avg_confidence,
                int(low_confidence_flag),
                analysis_context.strip(),
            ),
        )
        return int(cursor.lastrowid)


def _decode_submission(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite row into the dictionaries the templates expect."""
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "raw_input": json.loads(row["raw_input"]),
        "computed_drivers": json.loads(row["computed_drivers"]),
        "ai_output": json.loads(row["ai_output"]),
        "avg_confidence": float(row["avg_confidence"]),
        "low_confidence_flag": bool(row["low_confidence_flag"]),
        "analysis_context": row["analysis_context"] or "",
    }


def get_all_submissions() -> list[dict[str, Any]]:
    """Read all saved submissions, newest first."""
    with _connection() as connection:
        rows = connection.execute(
            "SELECT * FROM submissions ORDER BY id DESC"
        ).fetchall()
    return [_decode_submission(row) for row in rows]


def get_submission_by_id(submission_id: int) -> dict[str, Any] | None:
    """Read one saved submission by its numeric id, or return None."""
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM submissions WHERE id = ?",
            (submission_id,),
        ).fetchone()
    return _decode_submission(row) if row else None