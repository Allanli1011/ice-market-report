#!/usr/bin/env python3
"""
ICE Market Report Scraper - Main Entry Point

Scrapes ICE (Intercontinental Exchange) website for market report PDFs,
extracts data from the PDFs, and consolidates everything into an Excel file.

Usage:
    python main.py              # Run once immediately
    python main.py --schedule   # Run daily at the configured time (default 07:00)
    python main.py --time 08:30 # Run daily at a custom time
"""

import argparse
import logging
import sys
from datetime import datetime

import schedule
import time as time_module

import config
from scraper import ICEReportScraper
from pdf_parser import PDFParser
from excel_generator import ExcelGenerator
from trading_calendar import (
    TradingCalendar,
    extract_date_from_text,
    extract_date_from_filename,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.BASE_DIR / "ice_scraper.log"),
    ],
)
logger = logging.getLogger("ice-market-report")


def run_pipeline():
    """Execute the full scraping pipeline: scrape -> parse -> generate Excel."""
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("Starting ICE Market Report pipeline at %s", start_time)
    logger.info("=" * 60)

    try:
        # Step 1: Scrape and download PDFs
        logger.info("[Step 1/4] Scraping ICE website for market report PDFs...")
        scraper = ICEReportScraper()
        downloaded_files = scraper.run()

        if not downloaded_files:
            logger.warning("No PDF files were downloaded. Exiting pipeline.")
            return

        logger.info("Downloaded %d PDF files.", len(downloaded_files))

        # Step 2: Validate report dates against trading calendar
        logger.info("[Step 2/4] Validating report dates against trading calendar...")
        calendar = TradingCalendar()
        validated_files = _validate_report_dates(downloaded_files, calendar)

        if not validated_files:
            logger.warning(
                "No reports match the most recent trading day. "
                "ICE may not have published the latest reports yet. "
                "Aborting pipeline."
            )
            return

        logger.info(
            "Validated %d/%d files as current trading day reports.",
            len(validated_files), len(downloaded_files),
        )

        # Step 3: Parse PDFs
        logger.info("[Step 3/4] Parsing downloaded PDF files...")
        parser = PDFParser()
        parsed_data = parser.parse_multiple(validated_files)
        logger.info("Parsed %d PDF files.", len(parsed_data))

        # Step 4: Generate Excel
        logger.info("[Step 4/4] Generating consolidated Excel report...")
        generator = ExcelGenerator()
        output_path = generator.generate(parsed_data)

        elapsed = datetime.now() - start_time
        logger.info("=" * 60)
        logger.info("Pipeline complete!")
        logger.info("  PDFs downloaded: %d", len(downloaded_files))
        logger.info("  PDFs validated:  %d", len(validated_files))
        logger.info("  PDFs parsed:     %d", len(parsed_data))
        logger.info("  Excel output:    %s", output_path)
        logger.info("  Time elapsed:    %s", elapsed)
        logger.info("=" * 60)

    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        raise


def _validate_report_dates(
    downloaded_files: list[dict], calendar: TradingCalendar
) -> list[dict]:
    """Check each downloaded report to see if it's for the most recent trading day.

    Tries to extract the report date from the filename first, then from the
    PDF text content. If the report date doesn't match the expected trading
    day, it's excluded.

    Returns the list of files that match the expected trading day. If no dates
    can be determined from any file, returns all files (we can't validate).
    """
    logger = logging.getLogger("ice-market-report")
    today = datetime.now().date()

    validated = []
    undetermined = []
    stale_count = 0

    for file_info in downloaded_files:
        report_id = file_info.get("report_id")
        filename = file_info.get("filename", "")
        expected_date = calendar.get_expected_report_date(report_id, today)

        # Try filename first
        report_date = extract_date_from_filename(filename)

        # If no date in filename, do a quick text extraction from the PDF
        if report_date is None:
            filepath = file_info.get("filepath", "")
            if filepath:
                try:
                    import pdfplumber
                    with pdfplumber.open(filepath) as pdf:
                        if pdf.pages:
                            first_text = pdf.pages[0].extract_text() or ""
                            report_date = extract_date_from_text(first_text)
                except Exception:
                    pass

        if report_date is None:
            logger.info(
                "  %s: could not determine report date, keeping file",
                filename,
            )
            undetermined.append(file_info)
        elif report_date == expected_date:
            logger.info(
                "  %s: report date %s matches expected trading day",
                filename, report_date,
            )
            validated.append(file_info)
        else:
            logger.warning(
                "  %s: report date %s does NOT match expected trading day %s "
                "(stale report)",
                filename, report_date, expected_date,
            )
            stale_count += 1

    if stale_count > 0 and not validated:
        # All reports with determinable dates are stale
        logger.warning(
            "All %d dated reports are stale (not for trading day %s). "
            "ICE has likely not published today's reports yet.",
            stale_count, calendar.get_expected_report_date(reference_date=today),
        )
        return []

    # Include undetermined files alongside validated ones
    return validated + undetermined


def main():
    parser = argparse.ArgumentParser(
        description="ICE Market Report Scraper - Download and consolidate ICE market reports"
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run on a daily schedule instead of once",
    )
    parser.add_argument(
        "--time",
        type=str,
        default=config.SCHEDULE_TIME,
        help=f"Time to run daily (HH:MM format, default: {config.SCHEDULE_TIME})",
    )
    args = parser.parse_args()

    if args.schedule:
        logger.info("Scheduling daily run at %s", args.time)
        schedule.every().day.at(args.time).do(run_pipeline)

        # Also run immediately on first start
        logger.info("Running initial pipeline now...")
        run_pipeline()

        logger.info("Scheduler active. Waiting for next run at %s...", args.time)
        while True:
            schedule.run_pending()
            time_module.sleep(60)
    else:
        run_pipeline()


if __name__ == "__main__":
    main()
