"""Safe public and OAuth-backed Google Sheets import helpers."""

import csv
import json
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


GOOGLE_SHEET_URL = re.compile(
    r"^https?://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)(?:/.*)?$",
    re.IGNORECASE,
)
MAX_SHEET_BYTES = 10 * 1024 * 1024
MAX_PRIVATE_SHEET_CELLS = 1_000_000
CONNECTOR_HELPER = Path(__file__).with_name("google_sheets_connector.mjs")


def get_google_sheet_id(sheet_url: str) -> str:
    """Validate a docs.google.com spreadsheet URL and return its stable ID."""
    candidate = sheet_url.strip()
    parsed = urlparse(candidate)
    match = GOOGLE_SHEET_URL.match(candidate)
    if not match or parsed.netloc.casefold() != "docs.google.com":
        raise ValueError(
            "Paste a Google Sheets link from docs.google.com/spreadsheets."
        )
    return match.group(1)


def build_csv_export_url(sheet_url: str) -> str:
    """Validate a Google Sheets URL and turn it into a public CSV export URL."""
    spreadsheet_id = get_google_sheet_id(sheet_url)
    parsed = urlparse(sheet_url.strip())

    query_gid = parse_qs(parsed.query).get("gid", [""])[0]
    fragment_gid = parse_qs(parsed.fragment).get("gid", [""])[0]
    gid = query_gid or fragment_gid
    export_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv"
    return f"{export_url}&gid={gid}" if gid else export_url


def download_google_sheet(sheet_url: str, destination: Path) -> None:
    """Download a public Google Sheet CSV without accepting arbitrary URLs."""
    export_url = build_csv_export_url(sheet_url)
    request = Request(
        export_url,
        headers={"User-Agent": "Variance Explanation Assistant/1.0"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            content = response.read(MAX_SHEET_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as error:
        raise ValueError(
            "We could not download that sheet. Make sure it is shared publicly "
            "or published to the web."
        ) from error

    if len(content) > MAX_SHEET_BYTES:
        raise ValueError("Google Sheet exports must be 10 MB or smaller.")
    if b"<html" in content[:500].lower():
        raise ValueError(
            "Google returned a web page instead of CSV data. Share the sheet "
            "publicly or publish it to the web, then try again."
        )
    try:
        destination.write_bytes(content)
        content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        destination.unlink(missing_ok=True)
        raise ValueError("That Google Sheet is not a readable CSV export.") from error


def _connector_request(path: str) -> dict:
    """Call the managed Google Sheets OAuth proxy without handling tokens here."""
    try:
        completed = subprocess.run(
            ["node", str(CONNECTOR_HELPER)],
            input=json.dumps({"path": path}),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(
            "We could not reach the connected Google account. Please try again."
        ) from error

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(
            "We could not reach the connected Google account. Please try again."
        ) from error

    if completed.returncode != 0 or not result.get("ok"):
        status = result.get("status")
        if status in {401, 403, 404}:
            raise ValueError(
                "We could not open that private sheet with the connected Google "
                "account. Check the link and your access, then try again."
            )
        raise ValueError(
            "Google Sheets could not load that private sheet. Please try again."
        )
    return result["data"]


def get_private_sheet_metadata(sheet_url: str) -> dict:
    """Return only the accessible spreadsheet title and worksheet properties."""
    spreadsheet_id = get_google_sheet_id(sheet_url)
    metadata = _connector_request(
        f"/v4/spreadsheets/{spreadsheet_id}"
        "?fields=properties.title,spreadsheetUrl,sheets.properties"
    )
    sheets = [
        {
            "title": sheet.get("properties", {}).get("title", ""),
            "row_count": sheet.get("properties", {})
            .get("gridProperties", {})
            .get("rowCount", 0),
            "column_count": sheet.get("properties", {})
            .get("gridProperties", {})
            .get("columnCount", 0),
        }
        for sheet in metadata.get("sheets", [])
        if sheet.get("properties", {}).get("title")
    ]
    if not sheets:
        raise ValueError("That spreadsheet does not contain a readable worksheet.")
    return {
        "title": metadata.get("properties", {}).get("title", "Google Sheet"),
        "sheets": sheets,
    }


def _column_letter(index: int) -> str:
    """Convert a one-based spreadsheet index into an A1 column label."""
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def download_private_google_sheet(
    sheet_url: str, worksheet_title: str, destination: Path
) -> str:
    """Read one private worksheet through OAuth and write the shared CSV source."""
    metadata = get_private_sheet_metadata(sheet_url)
    worksheet = next(
        (sheet for sheet in metadata["sheets"] if sheet["title"] == worksheet_title),
        None,
    )
    if worksheet is None:
        raise ValueError("Choose a worksheet from the connected spreadsheet.")

    row_count = worksheet["row_count"]
    column_count = worksheet["column_count"]
    if not row_count or not column_count:
        raise ValueError("That worksheet is empty.")
    if row_count * column_count > MAX_PRIVATE_SHEET_CELLS:
        raise ValueError(
            "That worksheet is too large to import. Select a worksheet with a "
            "smaller table."
        )

    escaped_title = worksheet_title.replace("'", "''")
    a1_range = (
        f"'{escaped_title}'!A1:{_column_letter(column_count)}{row_count}"
    )
    from urllib.parse import quote

    values = _connector_request(
        f"/v4/spreadsheets/{get_google_sheet_id(sheet_url)}/values/"
        f"{quote(a1_range, safe='')}"
        "?majorDimension=ROWS&valueRenderOption=FORMATTED_VALUE"
    ).get("values", [])
    if not values:
        raise ValueError("That worksheet is empty.")

    try:
        with destination.open("w", encoding="utf-8", newline="") as source:
            csv.writer(source).writerows(values)
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise ValueError("We could not prepare that worksheet for mapping.") from error

    if destination.stat().st_size > MAX_SHEET_BYTES:
        destination.unlink(missing_ok=True)
        raise ValueError("Google Sheet exports must be 10 MB or smaller.")
    return metadata["title"]