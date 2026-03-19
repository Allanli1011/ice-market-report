"""
PDF Parser - Extracts data from downloaded ICE market report PDFs
and consolidates them into a structured format.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)


class PDFParser:
    """Parses ICE market report PDFs and extracts structured data."""

    def parse_pdf(self, filepath: str) -> dict:
        """Parse a single PDF file and extract its content.

        Returns a dict with:
            - metadata: file info, page count, title
            - tables: list of extracted tables
            - text_content: raw text from each page
        """
        filepath = Path(filepath)
        if not filepath.exists():
            logger.error("File not found: %s", filepath)
            return {}

        logger.info("Parsing PDF: %s", filepath.name)
        result = {
            "filename": filepath.name,
            "filepath": str(filepath),
            "parse_time": datetime.now().isoformat(),
            "metadata": {},
            "tables": [],
            "text_content": [],
        }

        try:
            with pdfplumber.open(filepath) as pdf:
                result["metadata"] = {
                    "page_count": len(pdf.pages),
                    "pdf_info": pdf.metadata or {},
                }

                for page_num, page in enumerate(pdf.pages, 1):
                    # Extract text
                    text = page.extract_text() or ""
                    if text.strip():
                        result["text_content"].append({
                            "page": page_num,
                            "text": text,
                        })

                    # Extract tables
                    tables = page.extract_tables() or []
                    for table_idx, table in enumerate(tables):
                        if table and len(table) > 0:
                            cleaned = self._clean_table(table)
                            if cleaned:
                                result["tables"].append({
                                    "page": page_num,
                                    "table_index": table_idx,
                                    "headers": cleaned[0] if cleaned else [],
                                    "rows": cleaned[1:] if len(cleaned) > 1 else [],
                                })

                # Try to extract report title from the first page
                if result["text_content"]:
                    first_page = result["text_content"][0]["text"]
                    result["metadata"]["title"] = self._extract_title(first_page)

        except Exception as e:
            logger.error("Error parsing %s: %s", filepath.name, e)
            result["error"] = str(e)

        logger.info(
            "Parsed %s: %d pages, %d tables extracted",
            filepath.name,
            result["metadata"].get("page_count", 0),
            len(result["tables"]),
        )
        return result

    def parse_multiple(self, file_list: list[dict]) -> list[dict]:
        """Parse multiple PDF files.

        Args:
            file_list: list of dicts with at least 'filepath' key

        Returns:
            list of parsed results
        """
        results = []
        for file_info in file_list:
            filepath = file_info.get("filepath", "")
            if not filepath:
                continue
            parsed = self.parse_pdf(filepath)
            if parsed:
                # Merge download metadata with parsed data
                parsed["download_info"] = {
                    k: v for k, v in file_info.items() if k != "filepath"
                }
                results.append(parsed)
        return results

    def _clean_table(self, table: list[list]) -> list[list[str]]:
        """Clean a raw table extracted from PDF."""
        cleaned = []
        for row in table:
            if row is None:
                continue
            cleaned_row = []
            for cell in row:
                if cell is None:
                    cleaned_row.append("")
                else:
                    # Clean whitespace and newlines
                    text = str(cell).replace("\n", " ").strip()
                    cleaned_row.append(text)
            # Skip completely empty rows
            if any(c.strip() for c in cleaned_row):
                cleaned.append(cleaned_row)
        return cleaned

    def _extract_title(self, first_page_text: str) -> str:
        """Try to extract the report title from the first page text."""
        lines = first_page_text.strip().split("\n")
        # Usually the title is in the first few non-empty lines
        for line in lines[:5]:
            line = line.strip()
            if len(line) > 5 and not line.startswith("Page"):
                # Skip lines that look like dates or page numbers
                if not re.match(r"^\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}$", line):
                    return line
        return "Unknown Report"
