# ICE Market Report Scraper

Automated pipeline that scrapes [ICE (Intercontinental Exchange)](https://www.ice.com) market report PDFs, extracts tabular data, and consolidates everything into a single Excel file.

## Features

- Scrapes ICE Report Center for available market report PDFs (End of Day, Volumes, Markers, etc.)
- Validates report dates against the ICE trading calendar to skip stale data
- Extracts tables and text from PDFs using `pdfplumber`
- Generates a formatted Excel workbook with summary, data, and raw text sheets
- Supports daily scheduled runs via `--schedule`

## Prerequisites

- Python 3.11+
- Chromium browser (installed automatically by Playwright)

## Setup

```bash
# Clone the repository
git clone https://github.com/Allanli1011/ice-market-report.git
cd ice-market-report

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

## Usage

```bash
# Run the pipeline once
python main.py

# Run on a daily schedule (default 07:00)
python main.py --schedule

# Run on a custom schedule
python main.py --schedule --time 08:30
```

### Environment Variables

| Variable            | Default | Description                          |
|---------------------|---------|--------------------------------------|
| `ICE_SCHEDULE_TIME` | `07:00` | Daily run time (HH:MM)              |
| `ICE_HEADLESS`      | `true`  | Run browser in headless mode         |

## Output

Excel reports are saved to `output/ice_market_reports_YYYY-MM-DD.xlsx` with three sheets:

| Sheet              | Content                                      |
|--------------------|----------------------------------------------|
| **Summary**        | Overview of all downloaded reports            |
| **All Reports Data** | Consolidated table data from all PDFs       |
| **Raw Text**       | Full text content extracted from each page    |

## Project Structure

```
ice-market-report/
├── main.py              # Entry point and pipeline orchestration
├── scraper.py           # Playwright-based ICE website scraper
├── pdf_parser.py        # PDF text and table extraction
├── excel_generator.py   # Excel report generation with formatting
├── trading_calendar.py  # ICE trading day validation
├── config.py            # Configuration constants and paths
├── test_pipeline.py     # Integration test with sample PDFs
├── requirements.txt     # Python dependencies
├── .gitignore
└── output/              # Generated Excel reports (git-ignored)
```

## Architecture

```
ICE Website ──► Scraper (Playwright) ──► PDF Downloads
                                              │
                                              ▼
                                    Trading Calendar Check
                                    (skip stale reports)
                                              │
                                              ▼
                                      PDF Parser (pdfplumber)
                                              │
                                              ▼
                                    Excel Generator (openpyxl)
                                              │
                                              ▼
                                      output/*.xlsx
```

The pipeline runs in four steps:

1. **Scrape** — Navigates the ICE Report Center with Playwright, discovers report pages, and downloads PDF files
2. **Validate** — Checks report dates against the `exchange_calendars` trading calendar; aborts if reports are stale
3. **Parse** — Extracts tables (structured and text-based) and metadata from each PDF
4. **Generate** — Writes all extracted data into a formatted Excel workbook

## Testing

```bash
# Run the integration test (creates sample PDFs and tests parse → Excel)
python test_pipeline.py
```

## Configured Reports

The scraper monitors these ICE report categories (see `config.py` for full list):

- End of Day reports (Europe, U.S., Canada, Endex, Singapore, Abu Dhabi)
- Historical volume reports (Futures & Options)
- Daily volume and open interest
- Brent markers
- Commitments of Traders
- Deliveries & Settlements

## License

Private repository — all rights reserved.
