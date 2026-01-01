# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""Tests for album scraper module.

Note: Tests for scraping functionality require Chrome/Selenium and are
marked with pytest.mark.integration. Run with: pytest -m integration
"""

import pytest
from unittest.mock import MagicMock, patch

from src.album_scraper import (
    MediaItem,
    AlbumScraper,
    scrape_albums,
)


class TestMediaItem:
    """Tests for MediaItem dataclass."""

    def test_default_values(self):
        """Test MediaItem with required fields only."""
        item = MediaItem(
            url="https://lh3.googleusercontent.com/pw/ABC123",
            media_type="photo",
            caption=None
        )
        assert item.url == "https://lh3.googleusercontent.com/pw/ABC123"
        assert item.media_type == "photo"
        assert item.caption is None
        assert item.thumbnail_url is None
        assert item.album_name is None

    def test_all_values(self):
        """Test MediaItem with all fields."""
        item = MediaItem(
            url="https://lh3.googleusercontent.com/pw/ABC123",
            media_type="video",
            caption="Test video",
            thumbnail_url="https://lh3.googleusercontent.com/pw/ABC123=w400-h300",
            album_name="Family Album"
        )
        assert item.media_type == "video"
        assert item.caption == "Test video"
        assert item.thumbnail_url == "https://lh3.googleusercontent.com/pw/ABC123=w400-h300"
        assert item.album_name == "Family Album"


class TestAlbumScraperUrlExtraction:
    """Tests for URL extraction and processing methods."""

    def test_extract_base_url_valid(self):
        """Test extracting base URL from full URL with params."""
        scraper = AlbumScraper()

        # URL with size params (path must be > 20 chars)
        url = "https://lh3.googleusercontent.com/pw/ABC123XYZ_LONGPATH_12345=w1920-h1080"
        base = scraper._extract_base_url(url)
        assert base == "https://lh3.googleusercontent.com/pw/ABC123XYZ_LONGPATH_12345"

        # URL without params
        url = "https://lh3.googleusercontent.com/pw/ABC123XYZ_LONGPATH_12345"
        base = scraper._extract_base_url(url)
        assert base == "https://lh3.googleusercontent.com/pw/ABC123XYZ_LONGPATH_12345"

    def test_extract_base_url_filters_profile_pics(self):
        """Test that profile picture URLs are filtered out."""
        scraper = AlbumScraper()

        # Profile picture patterns should return None
        assert scraper._extract_base_url("https://lh3.googleusercontent.com/a/default-user=s96") is None
        assert scraper._extract_base_url("https://lh3.googleusercontent.com/a-/ABC=s64") is None

        # Small icons should be filtered
        assert scraper._extract_base_url("https://lh3.googleusercontent.com/pw/ABC=s32") is None
        assert scraper._extract_base_url("https://lh3.googleusercontent.com/pw/ABC=s48-c-k") is None

    def test_extract_base_url_requires_pw_path(self):
        """Test that only /pw/ paths are accepted (album photos)."""
        scraper = AlbumScraper()

        # /pw/ path should be accepted
        url = "https://lh3.googleusercontent.com/pw/VALIDPATH123456789012345"
        assert scraper._extract_base_url(url) is not None

        # Other paths should be rejected
        url = "https://lh3.googleusercontent.com/a/ABC"
        assert scraper._extract_base_url(url) is None

    def test_extract_base_url_invalid(self):
        """Test invalid URLs return None."""
        scraper = AlbumScraper()

        assert scraper._extract_base_url(None) is None
        assert scraper._extract_base_url("") is None
        assert scraper._extract_base_url("not-a-url") is None
        assert scraper._extract_base_url("https://example.com/image.jpg") is None

    def test_get_full_resolution_url(self):
        """Test generating full resolution download URL."""
        scraper = AlbumScraper()
        base = "https://lh3.googleusercontent.com/pw/ABC123"
        result = scraper.get_full_resolution_url(base)
        assert result == "https://lh3.googleusercontent.com/pw/ABC123=s0"

    def test_get_video_download_url(self):
        """Test generating video download URL."""
        scraper = AlbumScraper()
        base = "https://lh3.googleusercontent.com/pw/VIDEO123"
        result = scraper.get_video_download_url(base)
        assert result == "https://lh3.googleusercontent.com/pw/VIDEO123=dv"

    def test_get_sized_url(self):
        """Test generating sized image URL."""
        scraper = AlbumScraper()
        base = "https://lh3.googleusercontent.com/pw/ABC123"
        result = scraper.get_sized_url(base, 800, 600)
        assert result == "https://lh3.googleusercontent.com/pw/ABC123=w800-h600"


class TestAlbumScraperInit:
    """Tests for AlbumScraper initialization."""

    def test_default_init(self):
        """Test default initialization values."""
        scraper = AlbumScraper()
        assert scraper.headless is True
        assert scraper.timeout == 30
        assert scraper._driver is None
        assert scraper._progress_callback is None

    def test_custom_init(self):
        """Test custom initialization values."""
        callback = MagicMock()
        scraper = AlbumScraper(
            headless=False,
            timeout=60,
            progress_callback=callback
        )
        assert scraper.headless is False
        assert scraper.timeout == 60
        assert scraper._progress_callback == callback

    def test_set_progress_callback(self):
        """Test setting progress callback after init."""
        scraper = AlbumScraper()
        callback = MagicMock()
        scraper.set_progress_callback(callback)
        assert scraper._progress_callback == callback

    def test_progress_callback_is_safe(self):
        """Test that progress callback errors don't crash scraping."""
        def bad_callback(stage, current, total):
            raise RuntimeError("Callback error")

        scraper = AlbumScraper(progress_callback=bad_callback)
        # Should not raise
        scraper._report_progress("test", 0, 0)


class TestAlbumScraperHelpers:
    """Tests for helper methods."""

    def test_resolve_short_url(self):
        """Test short URL resolution."""
        scraper = AlbumScraper()

        # Short URL should pass through (Selenium handles redirect)
        short = "https://photos.app.goo.gl/ABC123"
        assert scraper._resolve_short_url(short) == short

        # Full URL should pass through unchanged
        full = "https://photos.google.com/share/ABC123"
        assert scraper._resolve_short_url(full) == full


class TestScrapeAlbumsFunction:
    """Tests for the convenience scrape_albums function."""

    @patch.object(AlbumScraper, 'scrape_album')
    def test_scrape_multiple_albums(self, mock_scrape):
        """Test scraping multiple albums combines results."""
        mock_scrape.side_effect = [
            [MediaItem(url="url1", media_type="photo", caption=None)],
            [MediaItem(url="url2", media_type="photo", caption=None)],
        ]

        result = scrape_albums(["album1", "album2"])
        assert len(result) == 2
        assert mock_scrape.call_count == 2

    @patch.object(AlbumScraper, 'scrape_album')
    def test_scrape_albums_deduplicates(self, mock_scrape):
        """Test that duplicate URLs are removed."""
        mock_scrape.side_effect = [
            [MediaItem(url="same_url", media_type="photo", caption=None)],
            [MediaItem(url="same_url", media_type="photo", caption="caption")],
        ]

        result = scrape_albums(["album1", "album2"])
        assert len(result) == 1  # Duplicates removed

    @patch.object(AlbumScraper, 'scrape_album')
    def test_scrape_albums_handles_errors(self, mock_scrape):
        """Test that errors in one album don't stop others."""
        mock_scrape.side_effect = [
            Exception("Network error"),
            [MediaItem(url="url2", media_type="photo", caption=None)],
        ]

        result = scrape_albums(["bad_album", "good_album"])
        assert len(result) == 1  # Only good album's results
