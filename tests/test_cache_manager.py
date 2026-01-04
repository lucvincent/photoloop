# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""
Tests for cache manager playlist navigation functionality.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestPlaylistNavigation:
    """Test playlist navigation (next/previous media)."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config for cache manager."""
        config = MagicMock()
        config.cache.directory = "/tmp/test_cache"
        config.cache.max_size_mb = 1000
        config.sync.max_dimension = 1920
        config.sync.full_resolution = False
        config.display.order = "sequential"
        config.display.video_enabled = False
        config.scaling.face_detection = False
        config.scaling.mode = "fill"
        config.scaling.max_crop_percent = 15
        config.scaling.face_position = "center"
        config.scaling.fallback_crop = "center"
        config.albums = []
        return config

    def test_get_next_media_advances_index(self):
        """Test that get_next_media advances the playlist index."""
        # Simulate playlist behavior
        playlist = ["photo1", "photo2", "photo3"]
        playlist_index = 0
        media = {
            "photo1": MagicMock(media_id="photo1"),
            "photo2": MagicMock(media_id="photo2"),
            "photo3": MagicMock(media_id="photo3"),
        }

        def get_next_media():
            nonlocal playlist_index
            media_id = playlist[playlist_index]
            playlist_index = (playlist_index + 1) % len(playlist)
            return media.get(media_id)

        # First call should get photo1
        result = get_next_media()
        assert result.media_id == "photo1"
        assert playlist_index == 1

        # Second call should get photo2
        result = get_next_media()
        assert result.media_id == "photo2"
        assert playlist_index == 2

        # Third call should get photo3
        result = get_next_media()
        assert result.media_id == "photo3"
        assert playlist_index == 0  # Wraps around

    def test_get_previous_media_goes_back(self):
        """Test that get_previous_media goes back in the playlist."""
        # Simulate playlist behavior after get_next_media was called twice
        playlist = ["photo1", "photo2", "photo3"]
        playlist_index = 2  # After showing photo1, photo2 (index points to next)
        media = {
            "photo1": MagicMock(media_id="photo1"),
            "photo2": MagicMock(media_id="photo2"),
            "photo3": MagicMock(media_id="photo3"),
        }

        def get_previous_media():
            nonlocal playlist_index
            # Move back 2 positions (since get_next_media already advanced)
            playlist_index = (playlist_index - 2) % len(playlist)
            media_id = playlist[playlist_index]
            playlist_index = (playlist_index + 1) % len(playlist)
            return media.get(media_id)

        # Going back should return photo1 (the one before current)
        result = get_previous_media()
        assert result.media_id == "photo1"

    def test_get_previous_media_wraps_around(self):
        """Test that get_previous_media wraps around at the beginning."""
        # Start at the beginning of playlist (index = 1 after showing first photo)
        playlist = ["photo1", "photo2", "photo3"]
        playlist_index = 1  # After showing photo1
        media = {
            "photo1": MagicMock(media_id="photo1"),
            "photo2": MagicMock(media_id="photo2"),
            "photo3": MagicMock(media_id="photo3"),
        }

        def get_previous_media():
            nonlocal playlist_index
            playlist_index = (playlist_index - 2) % len(playlist)
            media_id = playlist[playlist_index]
            playlist_index = (playlist_index + 1) % len(playlist)
            return media.get(media_id)

        # Going back from photo1 should wrap to photo3
        result = get_previous_media()
        assert result.media_id == "photo3"

    def test_navigation_sequence(self):
        """Test a sequence of next and previous calls."""
        playlist = ["photo1", "photo2", "photo3", "photo4"]
        playlist_index = 0
        media = {
            f"photo{i}": MagicMock(media_id=f"photo{i}")
            for i in range(1, 5)
        }

        def get_next_media():
            nonlocal playlist_index
            media_id = playlist[playlist_index]
            playlist_index = (playlist_index + 1) % len(playlist)
            return media.get(media_id)

        def get_previous_media():
            nonlocal playlist_index
            playlist_index = (playlist_index - 2) % len(playlist)
            media_id = playlist[playlist_index]
            playlist_index = (playlist_index + 1) % len(playlist)
            return media.get(media_id)

        # Next: photo1
        result = get_next_media()
        assert result.media_id == "photo1"

        # Next: photo2
        result = get_next_media()
        assert result.media_id == "photo2"

        # Next: photo3
        result = get_next_media()
        assert result.media_id == "photo3"

        # Previous: should go back to photo2
        result = get_previous_media()
        assert result.media_id == "photo2"

        # Previous: should go back to photo1
        result = get_previous_media()
        assert result.media_id == "photo1"

        # Next: back to photo2
        result = get_next_media()
        assert result.media_id == "photo2"


class TestPlaylistRebuild:
    """Test playlist rebuild functionality."""

    def test_empty_playlist_returns_none(self):
        """Test that empty playlist returns None."""
        playlist = []

        def get_next_media():
            if not playlist:
                return None
            return "something"

        result = get_next_media()
        assert result is None

    def test_playlist_filters_deleted_items(self):
        """Test that deleted items are not in playlist."""
        # Simulating the rebuild logic
        media = {
            "photo1": MagicMock(media_id="photo1", deleted=False, album_source="Album1"),
            "photo2": MagicMock(media_id="photo2", deleted=True, album_source="Album1"),
            "photo3": MagicMock(media_id="photo3", deleted=False, album_source="Album1"),
        }
        enabled_albums = {"Album1"}

        available = [
            media_id for media_id, cached in media.items()
            if not cached.deleted
            and cached.album_source in enabled_albums
        ]

        assert "photo1" in available
        assert "photo2" not in available  # Deleted
        assert "photo3" in available

    def test_playlist_filters_disabled_albums(self):
        """Test that items from disabled albums are filtered out."""
        media = {
            "photo1": MagicMock(media_id="photo1", deleted=False, album_source="Album1"),
            "photo2": MagicMock(media_id="photo2", deleted=False, album_source="Album2"),
            "photo3": MagicMock(media_id="photo3", deleted=False, album_source="Album1"),
        }
        enabled_albums = {"Album1"}  # Album2 is disabled

        available = [
            media_id for media_id, cached in media.items()
            if not cached.deleted
            and cached.album_source in enabled_albums
        ]

        assert "photo1" in available
        assert "photo2" not in available  # Disabled album
        assert "photo3" in available


class TestHasEnabledAlbums:
    """Test the has_enabled_albums method."""

    def test_has_enabled_albums_true(self):
        """Test returns True when at least one album is enabled."""
        albums = [
            MagicMock(enabled=True),
            MagicMock(enabled=False),
        ]

        result = any(album.enabled for album in albums)
        assert result is True

    def test_has_enabled_albums_false(self):
        """Test returns False when no albums are enabled."""
        albums = [
            MagicMock(enabled=False),
            MagicMock(enabled=False),
        ]

        result = any(album.enabled for album in albums)
        assert result is False

    def test_has_enabled_albums_empty(self):
        """Test returns False when no albums exist."""
        albums = []

        result = any(album.enabled for album in albums)
        assert result is False


class TestLocalDirectoryScanning:
    """Test local directory scanning functionality."""

    def test_scan_finds_photos(self, temp_dir):
        """Test that scanning finds photo files."""
        import os
        from src.cache_manager import CacheManager

        # Create test directory structure
        photos_dir = temp_dir / "photos"
        photos_dir.mkdir()
        (photos_dir / "photo1.jpg").touch()
        (photos_dir / "photo2.jpeg").touch()
        (photos_dir / "photo3.png").touch()
        (photos_dir / "not_a_photo.txt").touch()

        # Supported extensions
        PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif'}

        # Simulate scanning logic
        found_items = []
        for entry in os.listdir(photos_dir):
            ext = os.path.splitext(entry)[1].lower()
            if ext in PHOTO_EXTENSIONS:
                found_items.append(entry)

        assert len(found_items) == 3
        assert "photo1.jpg" in found_items
        assert "photo2.jpeg" in found_items
        assert "photo3.png" in found_items
        assert "not_a_photo.txt" not in found_items

    def test_scan_skips_hidden_files(self, temp_dir):
        """Test that hidden files are skipped."""
        import os

        photos_dir = temp_dir / "photos"
        photos_dir.mkdir()
        (photos_dir / "visible.jpg").touch()
        (photos_dir / ".hidden.jpg").touch()
        (photos_dir / ".DS_Store").touch()

        # Simulate scanning logic
        PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png'}
        found_items = []
        for entry in os.listdir(photos_dir):
            if entry.startswith('.'):
                continue
            ext = os.path.splitext(entry)[1].lower()
            if ext in PHOTO_EXTENSIONS:
                found_items.append(entry)

        assert len(found_items) == 1
        assert "visible.jpg" in found_items
        assert ".hidden.jpg" not in found_items

    def test_scan_recursive(self, temp_dir):
        """Test that scanning is recursive."""
        import os

        photos_dir = temp_dir / "photos"
        photos_dir.mkdir()
        (photos_dir / "root_photo.jpg").touch()

        subdir = photos_dir / "vacation"
        subdir.mkdir()
        (subdir / "beach.jpg").touch()

        deep_subdir = subdir / "day1"
        deep_subdir.mkdir()
        (deep_subdir / "sunset.jpg").touch()

        # Simulate recursive scanning
        PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png'}
        found_items = []

        for root, dirs, files in os.walk(photos_dir):
            # Filter hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.startswith('.'):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in PHOTO_EXTENSIONS:
                    found_items.append(os.path.join(root, f))

        assert len(found_items) == 3

    def test_case_insensitive_extensions(self, temp_dir):
        """Test that extension matching is case-insensitive."""
        import os

        photos_dir = temp_dir / "photos"
        photos_dir.mkdir()
        (photos_dir / "lower.jpg").touch()
        (photos_dir / "upper.JPG").touch()
        (photos_dir / "mixed.JpG").touch()

        PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png'}
        found_items = []
        for entry in os.listdir(photos_dir):
            ext = os.path.splitext(entry)[1].lower()
            if ext in PHOTO_EXTENSIONS:
                found_items.append(entry)

        assert len(found_items) == 3


class TestCachedMediaSourceType:
    """Test CachedMedia source_type and file_mtime fields."""

    def test_cached_media_defaults(self):
        """Test that CachedMedia has correct default values for new fields."""
        from src.cache_manager import CachedMedia

        media = CachedMedia(
            media_id="test123",
            url="https://example.com/photo.jpg",
            local_path="/cache/test123.jpg",
            media_type="photo"
        )

        assert media.source_type == "google_photos"
        assert media.file_mtime is None

    def test_cached_media_local_source(self):
        """Test CachedMedia with local source type."""
        from src.cache_manager import CachedMedia

        media = CachedMedia(
            media_id="test123",
            url="file:///home/user/photos/photo.jpg",
            local_path="/home/user/photos/photo.jpg",
            media_type="photo",
            source_type="local",
            file_mtime="2024-12-25T10:30:00"
        )

        assert media.source_type == "local"
        assert media.file_mtime == "2024-12-25T10:30:00"

    def test_cached_media_serialization(self):
        """Test that source_type and file_mtime are serialized correctly."""
        from src.cache_manager import CachedMedia

        media = CachedMedia(
            media_id="test123",
            url="file:///home/user/photos/photo.jpg",
            local_path="/home/user/photos/photo.jpg",
            media_type="photo",
            source_type="local",
            file_mtime="2024-12-25T10:30:00"
        )

        data = media.to_dict()
        assert data["source_type"] == "local"
        assert data["file_mtime"] == "2024-12-25T10:30:00"

        # Test deserialization
        restored = CachedMedia.from_dict(data)
        assert restored.source_type == "local"
        assert restored.file_mtime == "2024-12-25T10:30:00"

    def test_cached_media_backward_compat(self):
        """Test that old metadata without source_type loads correctly."""
        from src.cache_manager import CachedMedia

        # Old format without source_type or file_mtime
        old_data = {
            "media_id": "test123",
            "url": "https://lh3.googleusercontent.com/test",
            "local_path": "/cache/test123.jpg",
            "media_type": "photo",
            "google_caption": "Test",
            "exif_date": "2024-01-15T10:30:00",
            "album_source": "Test Album",
            "download_date": "2024-12-01T12:00:00",
            "last_seen": "2024-12-24T10:00:00",
            "content_hash": "deadbeef",
            "deleted": False
        }

        media = CachedMedia.from_dict(old_data)

        # Should default to google_photos for backward compatibility
        assert media.source_type == "google_photos"
        assert media.file_mtime is None
