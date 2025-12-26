"""
Google Photos album scraper.
Extracts photo and video URLs from public Google Photos albums.

Optimized for low-memory environments (Raspberry Pi):
- Uses memory-constrained Chrome options
- Processes URLs in batches during scrolling
- Periodically clears performance logs to prevent memory buildup
"""

import gc
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set
from urllib.parse import urlparse, urljoin

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

logger = logging.getLogger(__name__)


@dataclass
class MediaItem:
    """Represents a photo or video from a Google Photos album."""
    url: str                    # Base URL (without size params)
    media_type: str             # "photo" or "video"
    caption: Optional[str]      # Caption/description if found
    thumbnail_url: Optional[str] = None  # Thumbnail URL


class AlbumScraper:
    """
    Scrapes public Google Photos albums to extract media URLs.

    Uses Selenium with headless Chrome to load the album page,
    scroll to trigger lazy-loading, and capture image URLs from
    network requests.
    """

    # Google's image CDN domain
    GOOGLE_IMAGE_CDN = "lh3.googleusercontent.com"
    GOOGLE_PHOTO_CDN = "photos.fife.usercontent.google.com"

    def __init__(self, headless: bool = True, timeout: int = 30,
                 progress_callback: Optional[Callable[[str, int, int], None]] = None):
        """
        Initialize the scraper.

        Args:
            headless: Run browser in headless mode (no GUI).
            timeout: Page load timeout in seconds.
            progress_callback: Optional callback(stage, current, total) for progress updates.
                              stage: "loading", "scrolling", "extracting"
                              current/total: progress numbers (e.g., URLs found)
        """
        self.headless = headless
        self.timeout = timeout
        self._driver: Optional[webdriver.Chrome] = None
        self._progress_callback = progress_callback

    def set_progress_callback(self, callback: Optional[Callable[[str, int, int], None]]) -> None:
        """Set or update the progress callback."""
        self._progress_callback = callback

    def _report_progress(self, stage: str, current: int = 0, total: int = 0) -> None:
        """Report progress via callback if set."""
        if self._progress_callback:
            try:
                self._progress_callback(stage, current, total)
            except Exception:
                pass  # Don't let callback errors break scraping

    def _init_driver(self) -> webdriver.Chrome:
        """Initialize Chrome WebDriver with appropriate options."""
        options = Options()

        if self.headless:
            options.add_argument("--headless=new")

        # Essential options for Raspberry Pi / Linux
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")

        # Memory optimization for Raspberry Pi (limited RAM)
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-plugins")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-translate")
        options.add_argument("--disable-default-apps")
        options.add_argument("--mute-audio")
        options.add_argument("--js-flags=--max-old-space-size=256")  # Limit V8 heap
        options.add_argument("--renderer-process-limit=1")

        # Use smaller window to reduce memory footprint
        options.add_argument("--window-size=1280,2000")

        # Enable performance logging to capture network requests
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        # User agent to appear as regular browser
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Common chromedriver paths on different systems
        chromedriver_paths = [
            "/usr/bin/chromedriver",
            "/usr/local/bin/chromedriver",
            "/usr/lib/chromium/chromedriver",
            "/usr/lib/chromium-browser/chromedriver",
            "/snap/bin/chromium.chromedriver",
        ]

        driver = None
        last_error = None

        # Method 1: Try default (let Selenium find chromedriver)
        try:
            driver = webdriver.Chrome(options=options)
        except WebDriverException as e:
            last_error = e
            logger.debug(f"Default chromedriver not found: {e}")

        # Method 2: Try known paths
        if driver is None:
            for path in chromedriver_paths:
                try:
                    service = Service(path)
                    driver = webdriver.Chrome(service=service, options=options)
                    logger.info(f"Using chromedriver from: {path}")
                    break
                except WebDriverException as e:
                    last_error = e
                    continue

        # Method 3: Try chromedriver-autoinstaller as fallback
        if driver is None:
            try:
                import chromedriver_autoinstaller
                chromedriver_autoinstaller.install()
                driver = webdriver.Chrome(options=options)
                logger.info("Using chromedriver from chromedriver-autoinstaller")
            except ImportError:
                logger.debug("chromedriver-autoinstaller not available")
            except Exception as e:
                last_error = e
                logger.debug(f"chromedriver-autoinstaller failed: {e}")

        if driver is None:
            logger.error(f"Failed to initialize Chrome driver: {last_error}")
            raise RuntimeError(
                "Could not initialize Chrome WebDriver. "
                "Ensure Chromium and chromedriver are installed. Try:\n"
                "  sudo apt install chromium chromium-driver\n"
                "or for older systems:\n"
                "  sudo apt install chromium-browser chromium-chromedriver"
            )

        driver.set_page_load_timeout(self.timeout)
        return driver

    def _resolve_short_url(self, url: str) -> str:
        """
        Resolve a short URL (photos.app.goo.gl) to full URL.

        Args:
            url: Short or full album URL.

        Returns:
            Full album URL.
        """
        if "photos.app.goo.gl" in url:
            # Let Selenium handle the redirect
            return url
        return url

    def _scroll_and_collect(self, driver: webdriver.Chrome, scroll_pause: float = 0.8) -> Set[str]:
        """
        Scroll through the page and collect image URLs from network requests.

        Google Photos uses virtualized scrolling - only images near the viewport
        are in the DOM. However, we can capture all image URLs from the browser's
        performance logs as they are requested during scrolling.

        Memory-optimized for large albums:
        - Processes logs incrementally and clears them
        - Runs garbage collection periodically
        - Uses smaller scroll increments

        Args:
            driver: WebDriver instance.
            scroll_pause: Time to wait between scrolls.

        Returns:
            Set of collected base URLs.
        """
        collected_urls = set()
        url_pattern = re.compile(r'https://lh3\.googleusercontent\.com/pw/[^\s"\'<>\\]+')

        def extract_from_performance_logs():
            """Extract image URLs from browser performance logs (clears logs after reading)."""
            new_urls = 0
            try:
                # get_log clears the log buffer, which is important for memory
                logs = driver.get_log("performance")
                for entry in logs:
                    message = entry.get("message", "")
                    # Quick check before regex
                    if "googleusercontent.com/pw/" not in message:
                        continue
                    # Extract URLs from the log message
                    for match in url_pattern.findall(message):
                        base_url = self._extract_base_url(match)
                        if base_url and base_url not in collected_urls:
                            collected_urls.add(base_url)
                            new_urls += 1
                # Clear reference to logs list
                del logs
            except Exception as e:
                logger.debug(f"Error extracting from performance logs: {e}")
            return new_urls

        # Find the scroll container (Google Photos uses a c-wiz element)
        scroll_container = driver.execute_script("""
            var cwiz = document.querySelectorAll('c-wiz');
            for (var i = 0; i < cwiz.length; i++) {
                if (cwiz[i].scrollHeight > 5000) {
                    return cwiz[i];
                }
            }
            return null;
        """)

        if not scroll_container:
            logger.warning("Could not find scroll container, falling back to window scroll")
            return self._extract_urls_from_page(driver)

        scroll_height = driver.execute_script("return arguments[0].scrollHeight", scroll_container)
        logger.info(f"Starting scroll and collect (container height: {scroll_height}px)...")

        # Extract URLs captured during initial load
        extract_from_performance_logs()
        logger.info(f"Initial extraction: {len(collected_urls)} URLs")
        self._report_progress("scrolling", len(collected_urls), scroll_height)

        # Scroll through the container
        scroll_position = 0
        scroll_increment = 1500  # Smaller increment for memory efficiency
        no_new_urls_count = 0
        max_no_new = 20  # Stop after 20 scrolls with no new URLs
        scroll_count = 0
        last_progress_log = 0

        while scroll_position < scroll_height and no_new_urls_count < max_no_new:
            scroll_position += scroll_increment
            scroll_count += 1

            driver.execute_script(
                "arguments[0].scrollTop = arguments[1]",
                scroll_container,
                scroll_position
            )

            # Wait for images to load
            time.sleep(scroll_pause)

            # Extract URLs from performance logs
            new_count = extract_from_performance_logs()

            if new_count > 0:
                no_new_urls_count = 0
            else:
                no_new_urls_count += 1

            # Log progress periodically (every 50 URLs or 30 scrolls)
            total_urls = len(collected_urls)
            if total_urls >= last_progress_log + 50 or scroll_count % 30 == 0:
                progress_pct = min(100, int(scroll_position / scroll_height * 100))
                logger.info(f"Progress: {progress_pct}% scrolled, {total_urls} URLs collected")
                last_progress_log = total_urls
                self._report_progress("scrolling", total_urls, scroll_height)

            # Check if scroll height changed (more content loaded)
            new_height = driver.execute_script("return arguments[0].scrollHeight", scroll_container)
            if new_height > scroll_height:
                scroll_height = new_height
                no_new_urls_count = 0

            # Periodic garbage collection for long-running scrapes
            if scroll_count % 50 == 0:
                gc.collect()

            # Safety limit for extremely large albums
            if scroll_position > 1000000:
                logger.warning("Reached maximum scroll limit (1M px)")
                break

        logger.info(f"Scroll complete. Collected {len(collected_urls)} unique URLs after {scroll_count} scrolls.")

        # Final cleanup
        gc.collect()

        return collected_urls

    def _extract_urls_from_page(self, driver: webdriver.Chrome) -> Set[str]:
        """
        Extract image URLs from the page source and network logs.

        Fallback method when scroll container isn't found.
        Memory-optimized: processes incrementally and clears references.

        Args:
            driver: WebDriver instance.

        Returns:
            Set of unique base URLs.
        """
        urls = set()
        # Only match album photo URLs with /pw/ path (filters out profile pics)
        url_pattern = re.compile(
            r'https://(?:lh3\.googleusercontent\.com|photos\.fife\.usercontent\.google\.com)/pw/[^\s"\'<>\\]+'
        )

        # Method 1: Extract from img elements (most reliable, low memory)
        try:
            img_elements = driver.find_elements(By.TAG_NAME, "img")
            for img in img_elements:
                src = img.get_attribute("src") or ""
                if self.GOOGLE_IMAGE_CDN in src or self.GOOGLE_PHOTO_CDN in src:
                    base_url = self._extract_base_url(src)
                    if base_url:
                        urls.add(base_url)
            del img_elements
        except Exception as e:
            logger.debug(f"Error extracting from img elements: {e}")

        # Method 2: Extract from data attributes
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, "[data-src]")
            for elem in elements:
                src = elem.get_attribute("data-src") or ""
                if self.GOOGLE_IMAGE_CDN in src or self.GOOGLE_PHOTO_CDN in src:
                    base_url = self._extract_base_url(src)
                    if base_url:
                        urls.add(base_url)
            del elements
        except Exception as e:
            logger.debug(f"Error extracting from data attributes: {e}")

        # Method 3: Try to get from performance logs (clears buffer)
        try:
            logs = driver.get_log("performance")
            for entry in logs:
                message = entry.get("message", "")
                if self.GOOGLE_IMAGE_CDN not in message and self.GOOGLE_PHOTO_CDN not in message:
                    continue
                for match in url_pattern.findall(message):
                    base_url = self._extract_base_url(match)
                    if base_url:
                        urls.add(base_url)
            del logs
        except Exception as e:
            logger.debug(f"Error extracting from performance logs: {e}")

        # Method 4: Extract from page source (last resort, uses more memory)
        # Only do this if we found very few URLs from other methods
        if len(urls) < 10:
            try:
                page_source = driver.page_source
                for match in url_pattern.findall(page_source):
                    base_url = self._extract_base_url(match)
                    if base_url:
                        urls.add(base_url)
                del page_source
            except Exception as e:
                logger.debug(f"Error extracting from page source: {e}")

        gc.collect()
        return urls

    def _extract_base_url(self, url: str) -> Optional[str]:
        """
        Extract the base URL without size/format parameters.

        Google Photos URLs have format: base_url=w1234-h5678-...
        We want just the base_url part.

        Args:
            url: Full URL with parameters.

        Returns:
            Base URL or None if invalid.
        """
        if not url:
            return None

        # Only accept album photo URLs (must have /pw/ path)
        # This filters out profile pictures which use /a/ or other paths
        if "/pw/" not in url:
            return None

        # Skip profile pictures, avatars, and UI elements
        skip_patterns = [
            "/a/default-user",      # Default user avatar
            "/a-/",                 # Profile picture path
            "/ACJPJp",              # UI element
            "=s32", "=s48", "=s64", "=s96", "=s128",  # Small icons/avatars
            "-c-k",                 # Circular crop (profile pics)
            "-cc-",                 # Another circular crop variant
            "-rw-c",                # Circular crop variant
            "-no-c",                # No circular crop but often profile-related
        ]
        for pattern in skip_patterns:
            if pattern in url:
                return None

        # Remove everything after = (size/format params)
        if "=" in url:
            base = url.split("=")[0]
        else:
            base = url

        # Verify it looks like a valid photo URL
        if self.GOOGLE_IMAGE_CDN not in base and self.GOOGLE_PHOTO_CDN not in base:
            return None

        # Should have a reasonable length (not just the domain)
        parsed = urlparse(base)
        if len(parsed.path) < 20:
            return None

        return base

    def _extract_captions(self, driver: webdriver.Chrome) -> dict:
        """
        Try to extract captions/descriptions from the page.

        Args:
            driver: WebDriver instance.

        Returns:
            Dict mapping possible identifiers to captions.
        """
        captions = {}

        try:
            # Look for caption elements (Google Photos uses various patterns)
            caption_selectors = [
                "[data-tooltip]",
                "[aria-label]",
                ".caption",
                "[class*='caption']",
            ]

            for selector in caption_selectors:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elements:
                    text = (
                        elem.get_attribute("data-tooltip") or
                        elem.get_attribute("aria-label") or
                        elem.text
                    )
                    if text and len(text) > 3 and len(text) < 500:
                        # Store with element position as rough key
                        rect = elem.rect
                        key = f"{rect.get('x', 0)}_{rect.get('y', 0)}"
                        captions[key] = text

        except Exception as e:
            logger.debug(f"Error extracting captions: {e}")

        return captions

    def scrape_album(self, album_url: str) -> List[MediaItem]:
        """
        Scrape a Google Photos album and return media items.

        Args:
            album_url: URL of the public Google Photos album.

        Returns:
            List of MediaItem objects.
        """
        logger.info(f"Scraping album: {album_url}")
        self._report_progress("loading", 0, 0)

        driver = None
        try:
            driver = self._init_driver()

            # Navigate to album
            driver.get(album_url)

            # Wait for album photo grid to load (not just any image like profile pics)
            logger.debug("Waiting for photo grid to load...")
            try:
                # Wait for images with Google Photos CDN URLs
                WebDriverWait(driver, self.timeout).until(
                    lambda d: d.execute_script("""
                        var imgs = document.querySelectorAll('img[src*="googleusercontent"]');
                        for (var i = 0; i < imgs.length; i++) {
                            // Look for photo images (have long base64-like paths), not profile pics (=s32)
                            var src = imgs[i].src || '';
                            if (src.length > 100 && !src.includes('=s32') && !src.includes('=s48')) {
                                return true;
                            }
                        }
                        return false;
                    """)
                )
            except TimeoutException:
                logger.warning("Timeout waiting for photo grid to load")

            # Give extra time for dynamic content to render fully
            time.sleep(3)

            # Wait for page height to stabilize (indicates grid has loaded)
            last_height = driver.execute_script("return document.body.scrollHeight")
            for _ in range(5):
                time.sleep(1)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height > last_height:
                    last_height = new_height
                else:
                    break
            logger.debug(f"Page height stabilized at {last_height}px")

            # Scroll through page and collect URLs as we go
            # (Google Photos uses virtual scrolling - only visible items are in DOM)
            logger.debug("Scrolling page and collecting URLs...")
            urls = self._scroll_and_collect(driver)

            # Also try extracting any remaining URLs from current page state
            additional_urls = self._extract_urls_from_page(driver)
            urls.update(additional_urls)

            # Try to get captions
            captions = self._extract_captions(driver)

            logger.info(f"Found {len(urls)} media URLs")
            self._report_progress("complete", len(urls), len(urls))

            # Convert to MediaItem objects
            items = []
            for url in urls:
                # Determine type (basic heuristic - videos have different paths)
                media_type = "video" if "/video/" in url else "photo"

                item = MediaItem(
                    url=url,
                    media_type=media_type,
                    caption=None,  # Caption matching is imprecise
                    thumbnail_url=f"{url}=w400-h300"  # Generate thumbnail URL
                )
                items.append(item)

            return items

        except Exception as e:
            logger.error(f"Error scraping album: {e}")
            raise

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            # Force garbage collection after browser cleanup
            gc.collect()

    def get_full_resolution_url(self, base_url: str) -> str:
        """
        Convert a base URL to full resolution download URL.

        Args:
            base_url: Base URL without parameters.

        Returns:
            URL that will return full resolution image.
        """
        # =s0 returns original size
        return f"{base_url}=s0"

    def get_video_download_url(self, base_url: str) -> str:
        """
        Convert a base URL to video download URL.

        Args:
            base_url: Base URL without parameters.

        Returns:
            URL that will return downloadable video.
        """
        # =dv returns downloadable video
        return f"{base_url}=dv"

    def get_sized_url(self, base_url: str, width: int, height: int) -> str:
        """
        Get URL for a specific size.

        Args:
            base_url: Base URL without parameters.
            width: Desired width.
            height: Desired height.

        Returns:
            URL that will return image at specified size.
        """
        return f"{base_url}=w{width}-h{height}"

    def fetch_captions(
        self,
        album_url: str,
        urls_to_fetch: Set[str],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Dict[str, Optional[str]]:
        """
        Fetch captions for specific photo URLs from a Google Photos album.

        This method opens each photo's detail view to extract its caption.
        It's slower than scraping but necessary since captions aren't in the grid view.

        Args:
            album_url: URL of the Google Photos album.
            urls_to_fetch: Set of base URLs to fetch captions for.
            progress_callback: Optional callback(current, total) for progress updates.

        Returns:
            Dict mapping base URLs to their captions (None if no caption).
        """
        if not urls_to_fetch:
            return {}

        captions: Dict[str, Optional[str]] = {}
        driver = None

        logger.info(f"Fetching captions for {len(urls_to_fetch)} photos...")

        try:
            driver = self._init_driver()
            driver.get(album_url)

            # Wait for page to load
            time.sleep(5)

            # Find the scroll container
            scroll_container = driver.execute_script("""
                var cwiz = document.querySelectorAll('c-wiz');
                for (var i = 0; i < cwiz.length; i++) {
                    if (cwiz[i].scrollHeight > 1000) {
                        return cwiz[i];
                    }
                }
                return null;
            """)

            if not scroll_container:
                logger.warning("Could not find scroll container for caption extraction")
                return captions

            urls_found = 0
            urls_needed = len(urls_to_fetch)
            scroll_position = 0
            scroll_increment = 800
            max_scroll = 2000000  # Safety limit
            no_new_photos_count = 0

            while urls_found < urls_needed and scroll_position < max_scroll:
                # Get all visible photo elements
                photo_elements = driver.execute_script("""
                    var imgs = document.querySelectorAll('img[src*="googleusercontent.com/pw/"]');
                    var result = [];
                    for (var i = 0; i < imgs.length; i++) {
                        var rect = imgs[i].getBoundingClientRect();
                        if (rect.top >= 0 && rect.bottom <= window.innerHeight) {
                            result.push({
                                element: imgs[i],
                                src: imgs[i].src
                            });
                        }
                    }
                    return result;
                """)

                found_new = False
                for photo_data in photo_elements:
                    try:
                        src = photo_data.get('src', '')
                        base_url = self._extract_base_url(src)

                        if base_url and base_url in urls_to_fetch and base_url not in captions:
                            # Click on this photo to open detail view
                            img_element = driver.execute_script(
                                "return document.querySelector('img[src=\"' + arguments[0] + '\"]');",
                                src
                            )

                            if img_element:
                                img_element.click()
                                time.sleep(2)

                                # Extract caption from detail view
                                caption = self._extract_caption_from_detail_view(driver)
                                captions[base_url] = caption

                                urls_found += 1
                                found_new = True

                                if caption:
                                    logger.debug(f"Found caption for photo: {caption[:50]}...")

                                if progress_callback:
                                    try:
                                        progress_callback(urls_found, urls_needed)
                                    except Exception:
                                        pass

                                # Close detail view (press Escape)
                                from selenium.webdriver.common.keys import Keys
                                driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
                                time.sleep(1)

                    except Exception as e:
                        logger.debug(f"Error extracting caption for photo: {e}")
                        continue

                if found_new:
                    no_new_photos_count = 0
                else:
                    no_new_photos_count += 1

                # Stop if we've scrolled a lot without finding new photos
                if no_new_photos_count > 30:
                    logger.debug("No new photos found after extended scrolling, stopping")
                    break

                # Scroll down to find more photos
                scroll_position += scroll_increment
                driver.execute_script(
                    "arguments[0].scrollTop = arguments[1]",
                    scroll_container,
                    scroll_position
                )
                time.sleep(0.5)

            logger.info(f"Fetched captions for {urls_found}/{urls_needed} photos")
            return captions

        except Exception as e:
            logger.error(f"Error fetching captions: {e}")
            return captions

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            gc.collect()

    def _extract_caption_from_detail_view(self, driver: webdriver.Chrome) -> Optional[str]:
        """
        Extract caption from the photo detail view.

        Args:
            driver: WebDriver with photo detail view open.

        Returns:
            Caption string or None if not found.
        """
        try:
            # Try to find the info button and click it
            info_buttons = driver.find_elements(
                By.CSS_SELECTOR,
                "[aria-label*='Info'], [aria-label*='info'], [data-tooltip*='Info']"
            )

            if info_buttons:
                info_buttons[0].click()
                time.sleep(1)

            # Look for description/caption in the info panel
            # Google Photos uses various selectors for the description field
            caption_selectors = [
                # Description field in info panel
                "[aria-label*='description']",
                "[aria-label*='Description']",
                "[data-type='description']",
                # Text areas that might contain captions
                "textarea[aria-label*='description']",
                "div[aria-label*='description']",
                # Common class patterns
                "[class*='description']",
                "[class*='caption']",
            ]

            for selector in caption_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in elements:
                        text = elem.text or elem.get_attribute('value') or elem.get_attribute('aria-label')
                        if text and len(text) > 2 and len(text) < 1000:
                            # Filter out UI labels
                            if text.lower() not in ['description', 'add a description', 'caption']:
                                return text.strip()
                except Exception:
                    continue

            # Also try searching the page for any visible text that looks like a caption
            # (visible in the detail view, not in the grid)
            try:
                page_text = driver.find_element(By.TAG_NAME, 'body').text
                lines = [l.strip() for l in page_text.split('\n') if l.strip()]
                # Look for lines that seem like captions (not too short, not too long, not UI text)
                skip_patterns = ['share', 'download', 'edit', 'delete', 'info', 'photo',
                                 'video', 'zoom', 'add to', 'create', 'more', 'options']
                for line in lines:
                    if (10 < len(line) < 500 and
                        not any(p in line.lower() for p in skip_patterns) and
                        not line.startswith('http')):
                        # This might be a caption
                        return line
            except Exception:
                pass

            return None

        except Exception as e:
            logger.debug(f"Error extracting caption from detail view: {e}")
            return None


def scrape_albums(album_urls: List[str], headless: bool = True) -> List[MediaItem]:
    """
    Convenience function to scrape multiple albums.

    Args:
        album_urls: List of album URLs to scrape.
        headless: Run browser in headless mode.

    Returns:
        Combined list of MediaItems from all albums.
    """
    scraper = AlbumScraper(headless=headless)
    all_items = []

    for url in album_urls:
        try:
            items = scraper.scrape_album(url)
            all_items.extend(items)
        except Exception as e:
            logger.error(f"Failed to scrape album {url}: {e}")

    # Remove duplicates based on URL
    seen_urls = set()
    unique_items = []
    for item in all_items:
        if item.url not in seen_urls:
            seen_urls.add(item.url)
            unique_items.append(item)

    return unique_items
