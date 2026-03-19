"""
ICE Market Report Scraper - Uses Playwright to navigate the ICE website,
discover available PDF reports, and download them.

Uses a persistent browser profile so cookies/sessions survive across runs.
On first load, if a CAPTCHA (Google reCAPTCHA v2) is detected, the script
pauses for the user to solve it manually in the visible browser window.
"""

import logging
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

import config

logger = logging.getLogger(__name__)

IGNORED_OPTION_LABEL_KEYWORDS = {
    "functional cookies",
    "performance cookies",
    "targeting cookies",
    "social media cookies",
    "cookie",
    "privacy",
    "consent",
    "checkbox label",
}

IGNORED_LINK_TEXT_KEYWORDS = {
    "click-through agreement",
    "agreement",
    "privacy notice",
    "cookie",
}


class ICEReportScraper:
    """Scrapes ICE (Intercontinental Exchange) website for market report PDFs."""

    def __init__(self, download_dir: Path = None):
        self.download_dir = download_dir or config.PDF_DIR
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.downloaded_files: list[dict] = []
        self.browser_profile_dir = config.BASE_DIR / ".browser_profile"
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self.session_state_path = self.browser_profile_dir / "storage_state.json"
        self._downloaded_urls: set[str] = set()

    def run(self) -> list[dict]:
        """Main entry point: scrape all configured reports and download PDFs."""
        logger.info("Starting ICE market report scraper...")
        today = datetime.now().strftime("%Y-%m-%d")
        today_dir = self.download_dir / today
        today_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self.browser_profile_dir),
                headless=config.HEADLESS,
                accept_downloads=True,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                args=["--disable-blink-features=AutomationControlled"],
            )
            context.set_default_timeout(config.BROWSER_TIMEOUT)

            try:
                self._restore_session_state(context)
                page = context.pages[0] if context.pages else context.new_page()

                # Step 0: Open a page to let the user clear any CAPTCHA first
                self._wait_for_captcha_clearance(page, context)

                # Step 1: Discover reports from the report center
                discovered = self._discover_reports_from_center(page)

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
                            page, context, report_id, report_name, today_dir
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

    def _wait_for_captcha_clearance(self, page, context):
        """Open ICE and wait for the user to clear any CAPTCHA.

        In headed mode the browser window is visible for the user to
        interact with. The script polls until the reCAPTCHA disappears.
        """
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
            self._persist_session_state(context)
            time.sleep(2)

        except PlaywrightTimeout as e:
            logger.warning("Timeout during initial page load: %s", e)

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

    def _discover_reports_from_center(self, page) -> set[int]:
        """Visit the ICE Report Center and discover available report IDs."""
        discovered_ids = set()
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

        return discovered_ids

    # ------------------------------------------------------------------ #
    # Per-Report Scraping
    # ------------------------------------------------------------------ #

    def _scrape_report_page(self, page, context, report_id: int, report_name: str,
                            save_dir: Path):
        """Visit a specific report page and download any available PDFs."""
        url = f"{config.ICE_BASE_URL}/report/{report_id}"
        logger.info("Scraping report %d: %s (%s)", report_id, report_name, url)

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
            self._persist_session_state(context)
            time.sleep(2)

            found_downloads = self._download_from_current_view(
                page, report_id, report_name, save_dir
            )
            option_downloads = self._download_from_option_matrix(
                page, report_id, report_name, save_dir
            )

            # If nothing was found, try a final fallback scan.
            if not found_downloads and not option_downloads:
                self._try_alternative_download(
                    page, report_id, report_name, save_dir
                )

        except (PlaywrightTimeout, Exception) as e:
            logger.warning("Error scraping report %d: %s", report_id, e)

    # ------------------------------------------------------------------ #
    # PDF Discovery & Download Helpers
    # ------------------------------------------------------------------ #

    def _download_from_current_view(
        self, page, report_id: int, report_name: str, save_dir: Path
    ) -> bool:
        """Download all visible PDF assets on the current page state."""
        found_any = False
        pdf_links = self._find_pdf_links(page)
        download_buttons = self._find_download_buttons(page)

        # Download PDFs from direct links.
        for pdf_url, link_text in pdf_links:
            found_any = True
            self._download_pdf_via_link(
                page, pdf_url, link_text, report_id, report_name, save_dir
            )

        # Try clicking download buttons that might trigger PDF downloads.
        for button in download_buttons:
            found_any = True
            self._download_pdf_via_button(
                page, button, report_id, report_name, save_dir
            )

        return found_any

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

    def _download_from_option_matrix(
        self, page, report_id: int, report_name: str, save_dir: Path
    ) -> bool:
        """Traverse checkbox/radio options that reveal report download links."""
        controls = page.locator(
            "main input[type='checkbox'], "
            "main input[type='radio'], "
            "[role='main'] input[type='checkbox'], "
            "[role='main'] input[type='radio'], "
            "[class*='report'] input[type='checkbox'], "
            "[class*='report'] input[type='radio']"
        )
        try:
            control_count = controls.count()
        except Exception:
            return False

        if control_count == 0:
            return False

        logger.info("  Found %d selectable option(s); traversing them...", control_count)
        downloaded_any = False
        seen_labels: set[str] = set()

        for idx in range(control_count):
            control = page.locator("input[type='checkbox'], input[type='radio']").nth(idx)
            descriptor = self._describe_option(control, idx)
            option_label = descriptor["label"]
            option_type = descriptor["type"]

            if (
                not option_label
                or self._should_ignore_option_label(option_label)
                or option_label.lower() in {"all", "select all", "all reports"}
                or option_label in seen_labels
            ):
                continue

            seen_labels.add(option_label)

            if not self._is_option_interactable(control):
                continue

            logger.info("  Exploring option: %s", option_label)
            was_checked = self._is_checked(control)

            if not was_checked:
                if not self._set_option_state(control, checked=True):
                    logger.info("  Could not activate option: %s", option_label)
                    continue
            else:
                logger.info("  Option already active, using current view.")

            self._wait_for_page_update(page)
            self._handle_accept_agreement(page)
            current_view_has_assets = self._download_from_current_view(
                page, report_id, report_name, save_dir
            )
            if current_view_has_assets:
                downloaded_any = True

            if option_type == "checkbox" and not was_checked:
                self._set_option_state(control, checked=False)
                self._wait_for_page_update(page)

        return downloaded_any

    def _describe_option(self, control, index: int) -> dict:
        """Extract a stable display label for a checkbox/radio option."""
        try:
            data = control.evaluate(
                """(node, idx) => {
                    const textFromLabel = () => {
                        if (node.id) {
                            const linked = document.querySelector(`label[for="${node.id}"]`);
                            if (linked && linked.innerText) {
                                return linked.innerText.trim();
                            }
                        }
                        const wrappingLabel = node.closest("label");
                        if (wrappingLabel && wrappingLabel.innerText) {
                            return wrappingLabel.innerText.trim();
                        }
                        const parentText = node.parentElement?.innerText || "";
                        return parentText.trim();
                    };

                    return {
                        index: idx,
                        type: node.type || "checkbox",
                        label: (
                            node.getAttribute("aria-label")
                            || textFromLabel()
                            || node.getAttribute("value")
                            || `option-${idx + 1}`
                        ).replace(/\\s+/g, " ").trim(),
                    };
                }""",
                index,
            )
            return data
        except Exception:
            return {
                "index": index,
                "type": "checkbox",
                "label": f"option-{index + 1}",
            }

    def _is_option_interactable(self, control) -> bool:
        """Whether an option can be clicked in the current DOM state."""
        try:
            return control.is_enabled()
        except Exception:
            return False

    def _is_checked(self, control) -> bool:
        """Best-effort check for checkbox/radio state."""
        try:
            return control.is_checked()
        except Exception:
            return False

    def _set_option_state(self, control, checked: bool) -> bool:
        """Toggle an option even when the native input is hidden/styled."""
        try:
            if checked:
                control.check(force=True)
            else:
                control.uncheck(force=True)
            return True
        except Exception:
            try:
                control.click(force=True)
                return True
            except Exception:
                return False

    def _wait_for_page_update(self, page):
        """Allow reactive report links to render after a selection changes."""
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            time.sleep(2)

    def _find_download_buttons(self, page) -> list:
        """Find download buttons that might trigger PDF downloads."""
        buttons = []
        selectors = [
            "main button:has-text('Download')",
            "main button:has-text('PDF')",
            "main button:has-text('Export')",
            "main a:has-text('Download PDF')",
            "main a:has-text('Download Report')",
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
            if pdf_url in self._downloaded_urls:
                return
            if self._should_ignore_download(link_text, pdf_url):
                return

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
                self._downloaded_urls.add(pdf_url)
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

            if download.url in self._downloaded_urls:
                download.cancel()
                return

            suggested = download.suggested_filename
            if not suggested.endswith(".pdf"):
                return
            if self._should_ignore_download(suggested, download.url):
                download.cancel()
                return

            safe_name = re.sub(r'[^\w\-_.]', '_', suggested)
            save_path = save_dir / safe_name
            download.save_as(str(save_path))
            self._downloaded_urls.add(download.url)

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

    def _persist_session_state(self, context):
        """Persist cookies/localStorage to supplement the browser profile."""
        try:
            context.storage_state(path=str(self.session_state_path))
            logger.info("Saved browser session state to %s", self.session_state_path)
        except Exception as e:
            logger.warning("Could not persist session state: %s", e)

    def _restore_session_state(self, context):
        """Restore previously saved cookies/localStorage before scraping."""
        if not self.session_state_path.exists():
            return

        try:
            state = json.loads(self.session_state_path.read_text())
        except Exception as e:
            logger.warning("Could not read saved browser session state: %s", e)
            return

        cookies = state.get("cookies") or []
        if cookies:
            try:
                context.add_cookies(cookies)
                logger.info("Restored %d cookies from saved session state.", len(cookies))
            except Exception as e:
                logger.warning("Could not restore cookies: %s", e)

        origins = state.get("origins") or []
        if not origins:
            return

        for origin_state in origins:
            origin = origin_state.get("origin")
            local_storage = origin_state.get("localStorage") or []
            if not origin or not local_storage:
                continue

            page = context.new_page()
            try:
                page.goto(origin, wait_until="domcontentloaded",
                          timeout=config.PAGE_LOAD_TIMEOUT)
                page.evaluate(
                    """entries => {
                        for (const entry of entries) {
                            localStorage.setItem(entry.name, entry.value);
                        }
                    }""",
                    local_storage,
                )
            except Exception as e:
                logger.debug("Could not restore localStorage for %s: %s", origin, e)
            finally:
                page.close()

    def _should_ignore_option_label(self, label: str) -> bool:
        """Filter out non-report controls such as cookie preferences."""
        normalized = re.sub(r"\s+", " ", label).strip().lower()
        return any(keyword in normalized for keyword in IGNORED_OPTION_LABEL_KEYWORDS)

    def _should_ignore_download(self, link_text: str, url: str) -> bool:
        """Skip non-report PDFs such as agreements or cookie documents."""
        haystack = f"{link_text} {url}".lower()
        return any(keyword in haystack for keyword in IGNORED_LINK_TEXT_KEYWORDS)


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
