"""
Google Photos album scraper.
Extracts photo and video URLs from public Google Photos albums.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Set
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

    def __init__(self, headless: bool = True, timeout: int = 30):
        """
        Initialize the scraper.

        Args:
            headless: Run browser in headless mode (no GUI).
            timeout: Page load timeout in seconds.
        """
        self.headless = headless
        self.timeout = timeout
        self._driver: Optional[webdriver.Chrome] = None

    def _init_driver(self) -> webdriver.Chrome:
        """Initialize Chrome WebDriver with appropriate options."""
        options = Options()

        if self.headless:
            options.add_argument("--headless=new")

        # Essential options for Raspberry Pi / Linux
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")

        # Set a large window to ensure all images load
        options.add_argument("--window-size=1920,10000")

        # Disable images initially to speed up page load
        # We'll capture URLs from network requests instead
        options.add_argument("--blink-settings=imagesEnabled=false")

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

    def _scroll_page(self, driver: webdriver.Chrome, scroll_pause: float = 1.0) -> None:
        """
        Scroll through the page to trigger lazy-loading of all images.

        Args:
            driver: WebDriver instance.
            scroll_pause: Time to wait between scrolls.
        """
        last_height = driver.execute_script("return document.body.scrollHeight")

        while True:
            # Scroll down
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

            # Wait for new content to load
            time.sleep(scroll_pause)

            # Calculate new scroll height
            new_height = driver.execute_script("return document.body.scrollHeight")

            if new_height == last_height:
                # Try scrolling a bit more to trigger any remaining lazy loads
                for _ in range(3):
                    driver.execute_script(
                        "window.scrollTo(0, document.body.scrollHeight + 1000);"
                    )
                    time.sleep(0.5)

                final_height = driver.execute_script("return document.body.scrollHeight")
                if final_height == new_height:
                    break
                new_height = final_height

            last_height = new_height

        # Scroll back to top
        driver.execute_script("window.scrollTo(0, 0);")

    def _extract_urls_from_page(self, driver: webdriver.Chrome) -> Set[str]:
        """
        Extract image URLs from the page source and network logs.

        Args:
            driver: WebDriver instance.

        Returns:
            Set of unique base URLs.
        """
        urls = set()

        # Method 1: Extract from page source using regex
        page_source = driver.page_source

        # Pattern for Google Photos image URLs
        patterns = [
            r'https://lh3\.googleusercontent\.com/[a-zA-Z0-9_-]+',
            r'https://photos\.fife\.usercontent\.google\.com/[^\s"\'<>]+',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, page_source)
            for match in matches:
                base_url = self._extract_base_url(match)
                if base_url:
                    urls.add(base_url)

        # Method 2: Extract from img elements
        try:
            img_elements = driver.find_elements(By.TAG_NAME, "img")
            for img in img_elements:
                src = img.get_attribute("src") or ""
                if self.GOOGLE_IMAGE_CDN in src or self.GOOGLE_PHOTO_CDN in src:
                    base_url = self._extract_base_url(src)
                    if base_url:
                        urls.add(base_url)
        except Exception as e:
            logger.debug(f"Error extracting from img elements: {e}")

        # Method 3: Extract from data attributes
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, "[data-src]")
            for elem in elements:
                src = elem.get_attribute("data-src") or ""
                if self.GOOGLE_IMAGE_CDN in src or self.GOOGLE_PHOTO_CDN in src:
                    base_url = self._extract_base_url(src)
                    if base_url:
                        urls.add(base_url)
        except Exception as e:
            logger.debug(f"Error extracting from data attributes: {e}")

        # Method 4: Try to get from performance logs
        try:
            logs = driver.get_log("performance")
            for entry in logs:
                message = entry.get("message", "")
                if self.GOOGLE_IMAGE_CDN in message or self.GOOGLE_PHOTO_CDN in message:
                    # Extract URLs from log messages
                    url_matches = re.findall(
                        r'https://(?:lh3\.googleusercontent\.com|photos\.fife\.usercontent\.google\.com)/[^\s"\'<>\\]+',
                        message
                    )
                    for match in url_matches:
                        base_url = self._extract_base_url(match)
                        if base_url:
                            urls.add(base_url)
        except Exception as e:
            logger.debug(f"Error extracting from performance logs: {e}")

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

        # Skip profile pictures and UI elements
        skip_patterns = [
            "/a/default-user",
            "/ACJPJp",
            "=s32", "=s48", "=s64", "=s96",  # Small icons
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

        driver = None
        try:
            driver = self._init_driver()

            # Navigate to album
            driver.get(album_url)

            # Wait for page to load
            try:
                WebDriverWait(driver, self.timeout).until(
                    EC.presence_of_element_located((By.TAG_NAME, "img"))
                )
            except TimeoutException:
                logger.warning("Timeout waiting for images to load")

            # Give extra time for dynamic content
            time.sleep(2)

            # Scroll to load all images
            logger.debug("Scrolling page to load all images...")
            self._scroll_page(driver)

            # Extract URLs
            logger.debug("Extracting image URLs...")
            urls = self._extract_urls_from_page(driver)

            # Try to get captions
            captions = self._extract_captions(driver)

            logger.info(f"Found {len(urls)} media URLs")

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
