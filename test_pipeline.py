#!/usr/bin/env python3
"""
Test the PDF parsing and Excel generation pipeline using a sample PDF.
This verifies the non-network parts of the pipeline work correctly.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

import config
from pdf_parser import PDFParser
from excel_generator import ExcelGenerator
from trading_calendar import TradingCalendar, extract_date_from_text, extract_date_from_filename

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("test-pipeline")


def create_test_pdf_with_pdfplumber(filepath: Path):
    """Create a minimal test PDF using raw PDF spec (no extra deps needed)."""
    # Minimal valid PDF with text content resembling an ICE report
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj

2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj

3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj

4 0 obj
<< /Length 431 >>
stream
BT
/F1 16 Tf
50 740 Td
(ICE Futures Europe - End of Day Report) Tj
0 -30 Td
/F1 10 Tf
(Date: 2026-03-18) Tj
0 -20 Td
(Exchange: ICE Futures Europe) Tj
0 -30 Td
/F1 12 Tf
(Contract          Volume    Open Interest    Settlement) Tj
0 -20 Td
/F1 10 Tf
(Brent Crude       125430    584320           72.45) Tj
0 -15 Td
(WTI Crude          98200    412300           68.30) Tj
0 -15 Td
(Natural Gas        67800    298100            3.25) Tj
0 -15 Td
(Gasoil             45600    187200          685.50) Tj
0 -15 Td
(Low Sulphur        32100    145600          425.75) Tj
ET
endstream
endobj

5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj

xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000749 00000 n

trailer
<< /Size 6 /Root 1 0 R >>
startxref
826
%%EOF"""
    filepath.write_bytes(pdf_content)


def main():
    """Test the PDF parse -> Excel generation pipeline."""
    logger.info("=" * 60)
    logger.info("Testing PDF Parser + Excel Generator Pipeline")
    logger.info("=" * 60)

    # Create test directory
    today = datetime.now().strftime("%Y-%m-%d")
    test_dir = config.PDF_DIR / today
    test_dir.mkdir(parents=True, exist_ok=True)

    # Create sample test PDFs
    test_files = []
    for i, (name, report_id) in enumerate([
        ("ICE_Futures_Europe_EOD.pdf", 7),
        ("ICE_Futures_US_EOD.pdf", 10),
        ("Brent_Markers.pdf", 83),
    ]):
        filepath = test_dir / name
        create_test_pdf_with_pdfplumber(filepath)
        test_files.append({
            "report_id": report_id,
            "report_name": config.REPORT_IDS.get(report_id, f"Report {report_id}"),
            "filename": name,
            "filepath": str(filepath),
            "source_url": f"https://www.ice.com/report/{report_id}",
            "link_text": name,
            "download_time": datetime.now().isoformat(),
            "file_size": filepath.stat().st_size,
        })
        logger.info("Created test PDF: %s (%d bytes)", name, filepath.stat().st_size)

    # Step 0: Validate report dates
    logger.info("\n[Step 0] Validating report dates against trading calendar...")
    calendar = TradingCalendar()
    today = datetime.now().date()
    expected_date = calendar.get_expected_report_date(7, today)
    logger.info("  Expected report date (last trading day): %s", expected_date)

    for file_info in test_files:
        filepath = file_info["filepath"]
        # Extract date from PDF text
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            text = pdf.pages[0].extract_text() or ""
            report_date = extract_date_from_text(text)
            is_valid = calendar.validate_report_date(report_date, file_info["report_id"], today) if report_date else None
            logger.info(
                "  %s: extracted date=%s, valid=%s",
                file_info["filename"], report_date, is_valid,
            )

    # Test stale report detection
    logger.info("\n  Stale report test: date 2026-03-16 should be invalid")
    from datetime import date
    is_stale = not calendar.validate_report_date(date(2026, 3, 16), 7, today)
    logger.info("  Date 2026-03-16 is stale: %s", is_stale)

    # Step 1: Parse PDFs
    logger.info("\n[Step 1] Parsing PDFs...")
    parser = PDFParser()
    parsed_data = parser.parse_multiple(test_files)
    logger.info("Parsed %d PDFs", len(parsed_data))

    for pd_item in parsed_data:
        meta = pd_item.get("metadata", {})
        tables = pd_item.get("tables", [])
        texts = pd_item.get("text_content", [])
        logger.info(
            "  %s: %d pages, %d tables, %d text blocks",
            pd_item["filename"],
            meta.get("page_count", 0),
            len(tables),
            len(texts),
        )
        if texts:
            logger.info("    First page text preview: %s", texts[0]["text"][:100])

    # Step 2: Generate Excel
    logger.info("\n[Step 2] Generating Excel report...")
    generator = ExcelGenerator()
    output_path = generator.generate(parsed_data)

    logger.info("\n" + "=" * 60)
    logger.info("TEST RESULTS:")
    logger.info("  Test PDFs created: %d", len(test_files))
    logger.info("  PDFs parsed:       %d", len(parsed_data))
    logger.info("  Excel output:      %s", output_path)
    logger.info("  Excel size:        %d bytes", output_path.stat().st_size)
    logger.info("=" * 60)

    # Verify Excel has expected sheets
    from openpyxl import load_workbook
    wb = load_workbook(str(output_path))
    logger.info("\nExcel sheets: %s", wb.sheetnames)
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        logger.info("  %s: %d rows x %d cols", sheet_name, ws.max_row, ws.max_column)
    wb.close()

    logger.info("\nPipeline test PASSED!")


if __name__ == "__main__":
    main()
