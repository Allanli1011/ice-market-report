"""Configuration for ICE Market Report Scraper."""

import os
from pathlib import Path

# Base URLs
ICE_BASE_URL = "https://www.ice.com"
ICE_REPORT_CENTER_URL = f"{ICE_BASE_URL}/marketdata/reports"

# Known report IDs and their categories
# These are publicly accessible report pages on ICE
REPORT_IDS = {
    # End of Day Reports
    7: "End of Day - ICE Futures Europe",
    10: "End of Day - ICE Futures U.S.",
    143: "End of Day - ICE Futures Canada",
    159: "End of Day - ICE Endex",
    160: "End of Day - ICE Futures Singapore",
    167: "End of Day - ICE Futures Abu Dhabi",
    254: "End of Day - ICE Futures (Additional)",
    # Volumes & Open Interest
    8: "Historical Monthly Volumes - Futures",
    9: "Historical Monthly Volumes - Options",
    26: "Historical Daily Volume - Futures",
    27: "Historical Daily Volume - Options",
    97: "Historical Daily Volume - Options (Alt)",
    176: "ICE Daily & MTD/QTD/YTD Volume and OI",
    # Markers
    83: "Markers - Brent",
    # Commitments of Traders
    278: "Commitments of Traders",
    # Deliveries & Settlements
    145: "Deliveries & Settlements",
    196: "Settlement Prices",
}

# Directory paths
BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
PDF_DIR = DOWNLOAD_DIR / "pdfs"
OUTPUT_DIR = BASE_DIR / "output"

# Ensure directories exist
DOWNLOAD_DIR.mkdir(exist_ok=True)
PDF_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Scheduler settings
SCHEDULE_TIME = os.getenv("ICE_SCHEDULE_TIME", "07:00")  # Default: 7 AM

# Browser settings
HEADLESS = os.getenv("ICE_HEADLESS", "true").lower() == "true"
BROWSER_TIMEOUT = 60000  # 60 seconds
PAGE_LOAD_TIMEOUT = 30000  # 30 seconds

# Output settings
EXCEL_FILENAME_TEMPLATE = "ice_market_reports_{date}.xlsx"
