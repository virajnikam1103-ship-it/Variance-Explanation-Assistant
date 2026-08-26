"""Safe, public Google Sheets CSV import helpers."""

import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


GOOGLE_SHEET_URL = re.compile(
    r"^https?://docs\.google\.com/spreadsheets/d/([^/?#]+)(?:/.*)?$",
    re.IGNORECASE,
)
MAX_SHEET_BYTES = 10 * 1024 * 1024


def build_csv_export_url(sheet_url: str) -> str:
    """Validate a Google Sheets URL and turn it into a public CSV export URL."""
    candidate = sheet_url.strip()
    parsed = urlparse(candidate)
    match = GOOGLE_SHEET_URL.match(candidate)
    if not match or parsed.netloc.casefold() != "docs.google.com":
        raise ValueError(
            "Paste a Google Sheets link from docs.google.com/spreadsheets."
        )

    query_gid = parse_qs(parsed.query).get("gid", [""])[0]
    fragment_gid = parse_qs(parsed.fragment).get("gid", [""])[0]
    gid = query_gid or fragment_gid
    export_url = f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv"
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