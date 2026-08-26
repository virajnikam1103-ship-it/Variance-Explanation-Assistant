"""Regression coverage for saved-report print-to-PDF behavior.

The browser's native print dialog cannot be automated from this test process.
Instead, this test verifies the actual saved-report response, A4 print-media
contract, and the real export-button handler that invokes ``window.print()``.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import pymupdf
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.print_page_options import PrintOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from werkzeug.serving import make_server


ARTIFACT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ARTIFACT_DIR))

import database  # noqa: E402
from app import app  # noqa: E402
from logic import compute_key_drivers  # noqa: E402


class SavedReportPrintTest(unittest.TestCase):
    """Exercise the report content and browser print contract together."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.original_database_path = database.DATABASE_PATH
        self.original_testing = app.config["TESTING"]
        self.server = None
        self.server_thread = None
        database.DATABASE_PATH = Path(self.temp_directory.name) / "reports.db"
        database.init_db()
        app.config["TESTING"] = True
        self.client = app.test_client()

        long_ledger_note = (
            "Supplier freight rates increased after the monsoon disruption, "
            "so the full explanation must wrap in the printable ledger rather "
            "than being truncated by the screen-only scroll container."
        )
        self.rows = [
            {
                "category": "Revenue",
                "forecast": 5_000_000,
                "actual": 4_700_000,
                "notes": "Pricing concession for the annual enterprise renewal.",
            },
            {
                "category": "Raw Materials",
                "forecast": 1_800_000,
                "actual": 2_050_000,
                "notes": long_ledger_note,
            },
            {
                "category": "Marketing",
                "forecast": 450_000,
                "actual": 520_000,
                "notes": "Campaign spend was moved forward to the quarter.",
            },
            {
                "category": "Logistics",
                "forecast": 380_000,
                "actual": 430_000,
                "notes": "Rate increase on regional deliveries.",
            },
            {
                "category": "Labor",
                "forecast": 620_000,
                "actual": 600_000,
                "notes": "Vacancies remained open for part of the quarter.",
            },
            {
                "category": "Technology",
                "forecast": 240_000,
                "actual": 260_000,
                "notes": "Additional cloud capacity for the reporting close.",
            },
        ]
        drivers = compute_key_drivers(self.rows)
        ai_output = [
            {
                "driver_category": driver["category"],
                "classification": driver["rule_based_tag"],
                "explanation": (
                    f"{driver['category']} changed against plan and requires "
                    "the supporting evidence to be retained in the PDF."
                ),
                "confidence": 54 if index == 0 else 86,
                "low_confidence": index == 0,
                "tag_mismatch": False,
            }
            for index, driver in enumerate(drivers)
        ]
        self.report_id = database.save_submission(
            self.rows,
            drivers,
            ai_output,
            analysis_context="Print regression fixture with a deliberately long ledger note.",
        )

    def tearDown(self) -> None:
        if self.server is not None:
            self.server.shutdown()
        if self.server_thread is not None:
            self.server_thread.join(timeout=5)
        database.DATABASE_PATH = self.original_database_path
        app.config["TESTING"] = self.original_testing
        self.temp_directory.cleanup()

    def test_saved_report_keeps_complete_printable_content(self) -> None:
        response = self.client.get(f"/reports/{self.report_id}")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Revenue Review", page)
        self.assertIn("FY ", page)
        self.assertIn("Total Forecast", page)
        self.assertIn("₹", page)
        self.assertIn("Explanation", page)
        self.assertIn("Evidence Note", page)
        self.assertIn("Insufficient Evidence", page)
        self.assertIn("Line Items Ledger", page)
        self.assertIn(
            "Supplier freight rates increased after the monsoon disruption",
            page,
        )
        self.assertEqual(page.count("<tr>"), len(self.rows) + 1)

    def test_a4_print_styles_preserve_cards_and_long_ledger_notes(self) -> None:
        stylesheet = (ARTIFACT_DIR / "static" / "style.css").read_text(
            encoding="utf-8"
        )
        print_styles = stylesheet.split("@media print", maxsplit=1)[1]
        for expected_rule in (
            "@page {",
            "size: A4;",
            ".app-sidebar,",
            ".sidebar-toggle,",
            ".header-actions,",
            "display: none !important;",
            ".result-card,",
            "break-inside: avoid;",
            "page-break-inside: avoid;",
            ".table-responsive {",
            "max-height: none !important;",
            "overflow: visible !important;",
            ".data-table td:last-child {",
            "white-space: normal;",
            "overflow-wrap: anywhere;",
        ):
            with self.subTest(rule=expected_rule):
                self.assertIn(expected_rule, print_styles)

    def _start_report_server(self) -> str:
        self.server = make_server("127.0.0.1", 0, app)
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.server_thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/reports/{self.report_id}"

    def _print_report_pdf(self, browser: str, report_url: str) -> bytes:
        print_options = PrintOptions()
        print_options.page_width = 21
        print_options.page_height = 29.7

        if browser == "Chromium":
            options = ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            driver = webdriver.Chrome(options=options)
        else:
            options = FirefoxOptions()
            options.add_argument("-headless")
            driver = webdriver.Firefox(options=options)

        try:
            driver.get(report_url)
            return base64.b64decode(driver.print_page(print_options))
        finally:
            driver.quit()

    def test_chromium_and_firefox_generate_a4_reports_without_clipping(self) -> None:
        missing_browsers = [
            browser
            for browser in ("chromium", "firefox")
            if shutil.which(browser) is None
        ]
        if missing_browsers:
            self.skipTest(
                "Browser PDF validation requires: " + ", ".join(missing_browsers)
            )

        report_url = self._start_report_server()
        expected_text = (
            "Supplier freight rates increased after the monsoon disruption, "
            "so the full explanation must wrap in the printable ledger rather "
            "than being truncated by the screen-only scroll container."
        )

        for browser in ("Chromium", "Firefox"):
            with self.subTest(browser=browser):
                document = pymupdf.open(
                    stream=self._print_report_pdf(browser, report_url),
                    filetype="pdf",
                )
                try:
                    self.assertGreater(document.page_count, 1)
                    combined_text = " ".join(
                        page.get_text().replace("\u00a0", " ")
                        for page in document
                    )
                    normalized_text = " ".join(combined_text.split())
                    normalized_upper_text = normalized_text.upper()

                    for marker in (
                        "REVENUE REVIEW",
                        "FY ",
                        "TOTAL FORECAST",
                        "₹50,00,000.00",
                        "VARIANCE OVERVIEW",
                        "EXPLANATION",
                        "EVIDENCE NOTE",
                        "INSUFFICIENT EVIDENCE",
                        "LINE ITEMS LEDGER",
                    ):
                        with self.subTest(browser=browser, marker=marker):
                            self.assertIn(marker, normalized_upper_text)
                    self.assertIn(
                        " ".join(expected_text.split()),
                        normalized_text,
                    )
                    for row in self.rows:
                        with self.subTest(
                            browser=browser,
                            ledger_note=row["notes"],
                        ):
                            self.assertIn(
                                " ".join(row["notes"].split()),
                                normalized_text,
                            )

                    for page in document:
                        self.assertAlmostEqual(page.rect.width, 595, delta=2)
                        self.assertAlmostEqual(page.rect.height, 842, delta=2)
                        out_of_bounds_words = [
                            word
                            for word in page.get_text("words")
                            if word[0] < 0
                            or word[1] < 0
                            or word[2] > page.rect.width
                            or word[3] > page.rect.height
                        ]
                        self.assertEqual(out_of_bounds_words, [])
                finally:
                    document.close()

    def test_export_button_handler_calls_window_print(self) -> None:
        script_path = ARTIFACT_DIR / "static" / "script.js"
        node_harness = r"""
            const assert = require("node:assert/strict");
            const fs = require("node:fs");
            const vm = require("node:vm");

            const domEvents = {};
            const windowEvents = {};
            let exportClick;
            let printCalls = 0;
            const exportButton = {
              dataset: { printTitle: "Revenue Review" },
              addEventListener: (event, handler) => {
                if (event === "click") exportClick = handler;
              },
            };
            const document = {
              title: "Variance Assistant | Financial Review Desk",
              addEventListener: (event, handler) => { domEvents[event] = handler; },
              getElementById: () => null,
              querySelector: (selector) =>
                selector === "[data-print-report]" ? exportButton : null,
            };
            const window = {
              addEventListener: (event, handler) => { windowEvents[event] = handler; },
              print: () => { printCalls += 1; },
            };

            vm.runInNewContext(
              fs.readFileSync(process.argv[1], "utf8"),
              { document, window, console, requestAnimationFrame: () => {} },
            );
            domEvents.DOMContentLoaded();
            exportClick();

            assert.equal(printCalls, 1, "Export PDF must invoke window.print()");
            assert.equal(document.title, "Revenue Review - Variance Report");
        """

        result = subprocess.run(
            ["node", "-e", node_harness, str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)