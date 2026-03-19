"""
ICE Market Report Scraper - Uses Playwright to navigate the ICE website,
discover available PDF reports, and download them.

Uses a persistent browser profile so cookies/sessions survive across runs.
On first load, if a CAPTCHA (Google reCAPTCHA v2) is detected, the script
pauses for the user to solve it manually in the visible browser window.
"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

import config

logger = logging.getLogger(__name__)


class ICEReportScraper:
    """Scrapes ICE (Intercontinental Exchange) website for market report PDFs."""

    def __init__(self, download_dir: Path = None):
        self.download_dir = download_dir or config.PDF_DIR
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.downloaded_files: list[dict] = []

    def run(self) -> list[dict]:
        """Main entry point: scrape all configured reports and download PDFs."""
        logger.info("Starting ICE market report scraper...")
        today = datetime.now().strftime("%Y-%m-%d")
        today_dir = self.download_dir / today
        today_dir.mkdir(parents=True, exist_ok=True)

        # Use a persistent browser profile so cookies/sessions survive across runs
        user_data_dir = config.BASE_DIR / ".browser_profile"
        user_data_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=config.HEADLESS,
                accept_downloads=True,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            context.set_default_timeout(config.BROWSER_TIMEOUT)

            try:
                # Step 0: Open a page to let the user clear any CAPTCHA first
                self._wait_for_captcha_clearance(context)

                # Step 1: Discover reports from the report center
                discovered = self._discover_reports_from_center(context)

                # Step 2: Scrape each known report page for PDF links
                all_report_ids = set(config.REPORT_IDS.keys())
                for rid in discovered:
                    all_report_ids.add(rid)

                for report_id in sorted(all_report_ids):
                    report_name = config.REPORT_IDS.get(
                        report_id, f"Report {report_id}"
                    )
                    try:
                        self._scrape_report_page(
                            context, report_id, report_name, today_dir
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to scrape report %s (%s): %s",
                            report_id, report_name, e,
                        )
            finally:
                context.close()

        logger.info(
            "Scraping complete. Downloaded %d PDF files.", len(self.downloaded_files)
        )
        return self.downloaded_files

    # ------------------------------------------------------------------ #
    # CAPTCHA / Agreement Handling
    # ------------------------------------------------------------------ #

    def _wait_for_captcha_clearance(self, context):
        """Open ICE and wait for the user to clear any CAPTCHA.

        In headed mode the browser window is visible for the user to
        interact with. The script polls until the reCAPTCHA disappears.
        """
        page = context.new_page()
        try:
            logger.info("Opening ICE website for initial session setup...")
            page.goto(config.ICE_REPORT_CENTER_URL, wait_until="domcontentloaded",
                      timeout=config.PAGE_LOAD_TIMEOUT)
            time.sleep(3)

            if self._is_captcha_present(page):
                if config.HEADLESS:
                    logger.error(
                        "CAPTCHA detected but running in headless mode! "
                        "Please re-run with ICE_HEADLESS=false so you can "
                        "manually solve the CAPTCHA."
                    )
                    raise RuntimeError("CAPTCHA detected in headless mode")

                logger.info("=" * 60)
                logger.info("CAPTCHA / reCAPTCHA detected!")
                logger.info("Please solve it in the browser window.")
                logger.info("The script will wait up to 120 seconds...")
                logger.info("=" * 60)

                # Poll every 3 seconds for up to 120 seconds
                for _ in range(40):
                    time.sleep(3)
                    if not self._is_captcha_present(page):
                        logger.info("CAPTCHA cleared! Continuing...")
                        break
                else:
                    logger.warning(
                        "Timed out waiting for CAPTCHA. Continuing anyway..."
                    )
            else:
                logger.info("No CAPTCHA detected. Session is good.")

            # Handle the "I ACCEPT" agreement if present
            self._handle_accept_agreement(page)
            time.sleep(2)

        except PlaywrightTimeout as e:
            logger.warning("Timeout during initial page load: %s", e)
        finally:
            page.close()

    def _is_captcha_present(self, page) -> bool:
        """Check if any CAPTCHA / bot protection is blocking the page."""
        captcha_indicators = [
            # Google reCAPTCHA v2 (the one ICE actually uses)
            "iframe[src*='google.com/recaptcha']",
            "iframe[src*='recaptcha/api2']",
            ".g-recaptcha",
            "#g-recaptcha",
            # Cloudflare Turnstile
            "#cf-wrapper",
            "iframe[src*='turnstile']",
            "iframe[src*='challenges.cloudflare.com']",
            "#challenge-running",
            "#challenge-stage",
            # Generic
            "iframe[src*='captcha']",
            "iframe[src*='challenge']",
        ]
        for selector in captcha_indicators:
            try:
                if page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue

        # Also check page title for common challenge pages
        try:
            title = page.title().lower()
            if any(kw in title for kw in [
                "just a moment", "attention required", "challenge",
            ]):
                return True
        except Exception:
            pass

        return False

    def _handle_accept_agreement(self, page):
        """Click 'Accept All Cookies' and 'I ACCEPT' buttons if present."""
        try:
            cookie_btn = page.query_selector(
                "button:has-text('Accept All Cookies')"
            )
            if cookie_btn and cookie_btn.is_visible():
                cookie_btn.click()
                time.sleep(1)
        except Exception:
            pass

        try:
            accept_btn = page.query_selector("button:has-text('I ACCEPT')")
            if accept_btn and accept_btn.is_visible():
                logger.info("  Accepting Click-Through Agreement...")
                accept_btn.click()
                time.sleep(2)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Report Discovery
    # ------------------------------------------------------------------ #

    def _discover_reports_from_center(self, context) -> set[int]:
        """Visit the ICE Report Center and discover available report IDs."""
        discovered_ids = set()
        page = context.new_page()
        try:
            logger.info("Visiting ICE Report Center to discover reports...")
            page.goto(config.ICE_REPORT_CENTER_URL, wait_until="domcontentloaded",
                      timeout=config.PAGE_LOAD_TIMEOUT)
            self._handle_accept_agreement(page)
            # Wait for SPA content to render
            time.sleep(5)
            try:
                page.wait_for_selector("a[href*='/report/']", timeout=10000)
            except PlaywrightTimeout:
                logger.info("No report links found after waiting, continuing...")

            links = page.query_selector_all("a[href*='/report/']")
            for link in links:
                href = link.get_attribute("href") or ""
                match = re.search(r"/report/(\d+)", href)
                if match:
                    rid = int(match.group(1))
                    discovered_ids.add(rid)
                    text = (link.inner_text() or "").strip()
                    if rid not in config.REPORT_IDS and text:
                        config.REPORT_IDS[rid] = text
                        logger.info("Discovered new report: %d - %s", rid, text)

            logger.info(
                "Discovered %d report IDs from Report Center.",
                len(discovered_ids),
            )
        except (PlaywrightTimeout, Exception) as e:
            logger.warning("Could not fully load Report Center: %s", e)
        finally:
            page.close()

        return discovered_ids

    # ------------------------------------------------------------------ #
    # Per-Report Scraping
    # ------------------------------------------------------------------ #

    def _scrape_report_page(self, context, report_id: int, report_name: str,
                            save_dir: Path):
        """Visit a specific report page and download any available PDFs."""
        url = f"{config.ICE_BASE_URL}/report/{report_id}"
        logger.info("Scraping report %d: %s (%s)", report_id, report_name, url)

        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=config.PAGE_LOAD_TIMEOUT)
            time.sleep(3)

            # If reCAPTCHA pops up on this specific page, wait for it
            if self._is_captcha_present(page):
                if not config.HEADLESS:
                    logger.info(
                        "  reCAPTCHA detected on report %d. "
                        "Please solve it in the browser (waiting 120s)...",
                        report_id,
                    )
                    for _ in range(40):
                        time.sleep(3)
                        if not self._is_captcha_present(page):
                            logger.info("  reCAPTCHA cleared!")
                            break
                else:
                    logger.warning(
                        "  reCAPTCHA on report %d in headless mode, skipping.",
                        report_id,
                    )
                    return

            # Handle cookies / agreement overlay
            self._handle_accept_agreement(page)
            time.sleep(2)

            pdf_links = self._find_pdf_links(page)
            download_buttons = self._find_download_buttons(page)

            # Download PDFs from direct links
            for pdf_url, link_text in pdf_links:
                self._download_pdf_via_link(
                    page, pdf_url, link_text, report_id, report_name, save_dir
                )

            # Try clicking download buttons that might trigger PDF downloads
            for button in download_buttons:
                self._download_pdf_via_button(
                    page, button, report_id, report_name, save_dir
                )

            # If no PDFs found via links/buttons, try alternatives
            if not pdf_links and not download_buttons:
                self._try_alternative_download(
                    page, report_id, report_name, save_dir
                )

        except (PlaywrightTimeout, Exception) as e:
            logger.warning("Error scraping report %d: %s", report_id, e)
        finally:
            page.close()

    # ------------------------------------------------------------------ #
    # PDF Discovery & Download Helpers
    # ------------------------------------------------------------------ #

    def _find_pdf_links(self, page) -> list[tuple[str, str]]:
        """Find all PDF download links on the page."""
        pdf_links = []
        selectors = [
            "a[href$='.pdf']",
            "a[href*='.pdf?']",
            "a[href*='download'][href*='pdf']",
            "a[href*='publicdocs']",
        ]

        seen_urls = set()
        for selector in selectors:
            try:
                links = page.query_selector_all(selector)
                for link in links:
                    href = link.get_attribute("href") or ""
                    if href and href not in seen_urls:
                        seen_urls.add(href)
                        text = (link.inner_text() or "").strip()
                        if not text:
                            text = link.get_attribute("title") or "report"
                        full_url = urljoin(config.ICE_BASE_URL, href)
                        pdf_links.append((full_url, text))
            except Exception:
                continue

        return pdf_links

    def _find_download_buttons(self, page) -> list:
        """Find download buttons that might trigger PDF downloads."""
        buttons = []
        selectors = [
            "button:has-text('Download')",
            "button:has-text('PDF')",
            "button:has-text('Export')",
            "a:has-text('Download PDF')",
            "a:has-text('Download Report')",
            "[class*='download']",
            "[data-action*='download']",
        ]

        for selector in selectors:
            try:
                elements = page.query_selector_all(selector)
                buttons.extend(elements)
            except Exception:
                continue

        return buttons

    def _download_pdf_via_link(self, page, pdf_url: str, link_text: str,
                               report_id: int, report_name: str, save_dir: Path):
        """Download a PDF file from a direct URL."""
        try:
            url_filename = Path(urlparse(pdf_url).path).name
            if not url_filename.endswith(".pdf"):
                url_filename = f"report_{report_id}_{link_text[:30]}.pdf"

            safe_name = re.sub(r'[^\w\-_.]', '_', url_filename)
            save_path = save_dir / safe_name

            if save_path.exists():
                logger.info("File already exists, skipping: %s", save_path.name)
                return

            response = page.request.get(pdf_url)
            if response.ok:
                save_path.write_bytes(response.body())
                file_info = {
                    "report_id": report_id,
                    "report_name": report_name,
                    "filename": safe_name,
                    "filepath": str(save_path),
                    "source_url": pdf_url,
                    "link_text": link_text,
                    "download_time": datetime.now().isoformat(),
                    "file_size": save_path.stat().st_size,
                }
                self.downloaded_files.append(file_info)
                logger.info(
                    "Downloaded: %s (%d bytes)", safe_name, file_info["file_size"]
                )
            else:
                logger.warning(
                    "Failed to download %s: HTTP %d", pdf_url, response.status
                )
        except Exception as e:
            logger.error("Error downloading %s: %s", pdf_url, e)

    def _download_pdf_via_button(self, page, button, report_id: int,
                                  report_name: str, save_dir: Path):
        """Click a download button and save the resulting PDF."""
        try:
            with page.expect_download(timeout=15000) as download_info:
                button.click()
            download = download_info.value

            suggested = download.suggested_filename
            if not suggested.endswith(".pdf"):
                return

            safe_name = re.sub(r'[^\w\-_.]', '_', suggested)
            save_path = save_dir / safe_name
            download.save_as(str(save_path))

            file_info = {
                "report_id": report_id,
                "report_name": report_name,
                "filename": safe_name,
                "filepath": str(save_path),
                "source_url": download.url,
                "link_text": suggested,
                "download_time": datetime.now().isoformat(),
                "file_size": save_path.stat().st_size,
            }
            self.downloaded_files.append(file_info)
            logger.info("Downloaded via button: %s", safe_name)
        except (PlaywrightTimeout, Exception):
            pass

    def _try_alternative_download(self, page, report_id: int,
                                   report_name: str, save_dir: Path):
        """Try alternative methods to find and download PDFs."""
        # Check for iframes that might contain reports
        try:
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                frame_url = frame.url
                if ".pdf" in frame_url:
                    self._download_pdf_via_link(
                        page, frame_url, "embedded_report",
                        report_id, report_name, save_dir
                    )
        except Exception:
            pass

        # Check for data-href or onclick attributes with PDF URLs
        try:
            elements = page.query_selector_all(
                "[data-href*='.pdf'], [onclick*='.pdf']"
            )
            for el in elements:
                href = el.get_attribute("data-href") or ""
                if not href:
                    onclick = el.get_attribute("onclick") or ""
                    pdf_match = re.search(
                        r"(https?://[^\s'\"]+\.pdf)", onclick
                    )
                    if pdf_match:
                        href = pdf_match.group(1)
                if href:
                    full_url = urljoin(config.ICE_BASE_URL, href)
                    self._download_pdf_via_link(
                        page, full_url, "data_href_pdf",
                        report_id, report_name, save_dir
                    )
        except Exception:
            pass


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    scraper = ICEReportScraper()
    results = scraper.run()
    print(f"\nDownloaded {len(results)} PDF files:")
    for r in results:
        print(f"  - {r['filename']} ({r['report_name']})")
