# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""News headline provider using RSS feeds."""

import logging
import threading
import time
import xml.etree.ElementTree as ET
from html import unescape
from typing import List, Optional, TYPE_CHECKING

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

if TYPE_CHECKING:
    from ...config import NewsConfig

logger = logging.getLogger(__name__)


class NewsProvider:
    """Fetches news headlines from RSS feeds with rotation."""

    def __init__(self, config: 'NewsConfig'):
        """Initialize the news provider.

        Args:
            config: News configuration with feed URLs and settings.
        """
        self._config = config
        self._headlines: List[str] = []
        self._cache_time: float = 0
        self._lock = threading.Lock()

        # Get active feeds (filter out commented lines starting with #)
        active_feeds = self._get_active_feeds()

        # Start background update thread if we have active feeds
        if active_feeds:
            logger.info(f"News provider starting with {len(active_feeds)} active feed(s)")
            self._start_background_updates()
        else:
            logger.info("News provider initialized but no active feeds configured")

    def _get_active_feeds(self) -> List[str]:
        """Get list of active feed URLs (excluding commented lines)."""
        return [url for url in self._config.feed_urls
                if url and not url.strip().startswith('#')]

    def _start_background_updates(self) -> None:
        """Start background thread for news updates."""
        thread = threading.Thread(target=self._background_update_loop, daemon=True)
        thread.start()

    def _background_update_loop(self) -> None:
        """Background loop to fetch news headlines."""
        # Use configured refresh interval (default 5 minutes)
        interval_seconds = getattr(self._config, 'refresh_interval_minutes', 5) * 60
        while True:
            try:
                self._fetch_headlines()
            except Exception as e:
                logger.debug(f"Background news fetch failed: {e}")
            time.sleep(interval_seconds)

    def _fetch_headlines(self) -> None:
        """Fetch headlines from all configured RSS feeds."""
        if not REQUESTS_AVAILABLE:
            logger.warning("requests library not available for news")
            return

        active_feeds = self._get_active_feeds()
        if not active_feeds:
            return

        all_headlines = []

        for feed_url in active_feeds:
            try:
                headlines = self._parse_feed(feed_url)
                all_headlines.extend(headlines)
            except Exception as e:
                logger.debug(f"Failed to fetch feed {feed_url}: {e}")

        with self._lock:
            if all_headlines:
                # Limit to max headlines
                self._headlines = all_headlines[:self._config.max_headlines]
                self._cache_time = time.time()
                logger.info(f"News: fetched {len(self._headlines)} headlines from {len(active_feeds)} feed(s)")

    def _parse_feed(self, feed_url: str) -> List[str]:
        """Parse an RSS feed and extract headlines.

        Args:
            feed_url: URL of the RSS feed.

        Returns:
            List of headline strings.
        """
        response = requests.get(feed_url, timeout=10)
        response.raise_for_status()

        headlines = []
        root = ET.fromstring(response.content)

        # Handle both RSS 2.0 and Atom feeds
        # RSS 2.0: channel/item/title
        for item in root.findall('.//item/title'):
            if item.text:
                headline = self._clean_headline(item.text)
                if headline:
                    headlines.append(headline)

        # Atom: entry/title
        if not headlines:
            # Try Atom namespace
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for entry in root.findall('.//atom:entry/atom:title', ns):
                if entry.text:
                    headline = self._clean_headline(entry.text)
                    if headline:
                        headlines.append(headline)

            # Try without namespace (some feeds don't use namespaces)
            if not headlines:
                for entry in root.findall('.//entry/title'):
                    if entry.text:
                        headline = self._clean_headline(entry.text)
                        if headline:
                            headlines.append(headline)

        return headlines

    def _clean_headline(self, text: str) -> str:
        """Clean and truncate a headline.

        Args:
            text: Raw headline text.

        Returns:
            Cleaned headline string.
        """
        # Decode HTML entities
        text = unescape(text)
        # Strip whitespace
        text = text.strip()
        # Remove newlines
        text = ' '.join(text.split())
        # Truncate if too long (for display)
        max_len = 100
        if len(text) > max_len:
            text = text[:max_len - 3] + "..."
        return text

    def get_current_headline(self) -> Optional[str]:
        """Get the first headline for display (deprecated - use get_all_headlines for ticker).

        Returns:
            First headline string or None if no headlines.
        """
        # Fetch if cache is empty
        if not self._headlines and self._config.feed_urls:
            self._fetch_headlines()

        with self._lock:
            if not self._headlines:
                return None
            return self._headlines[0]

    def get_all_headlines(self) -> List[str]:
        """Get all cached headlines."""
        with self._lock:
            return list(self._headlines)

    def force_refresh(self) -> None:
        """Force an immediate refresh of headlines."""
        self._fetch_headlines()
