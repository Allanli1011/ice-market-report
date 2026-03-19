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
        logger.info("[Step 1/3] Scraping ICE website for market report PDFs...")
        scraper = ICEReportScraper()
        downloaded_files = scraper.run()

        if not downloaded_files:
            logger.warning("No PDF files were downloaded. Exiting pipeline.")
            return

        logger.info("Downloaded %d PDF files.", len(downloaded_files))

        # Step 2: Parse PDFs
        logger.info("[Step 2/3] Parsing downloaded PDF files...")
        parser = PDFParser()
        parsed_data = parser.parse_multiple(downloaded_files)
        logger.info("Parsed %d PDF files.", len(parsed_data))

        # Step 3: Generate Excel
        logger.info("[Step 3/3] Generating consolidated Excel report...")
        generator = ExcelGenerator()
        output_path = generator.generate(parsed_data)

        elapsed = datetime.now() - start_time
        logger.info("=" * 60)
        logger.info("Pipeline complete!")
        logger.info("  PDFs downloaded: %d", len(downloaded_files))
        logger.info("  PDFs parsed:     %d", len(parsed_data))
        logger.info("  Excel output:    %s", output_path)
        logger.info("  Time elapsed:    %s", elapsed)
        logger.info("=" * 60)

    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        raise


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
