"""
System health checks for PhotoLoop.

These tests are designed to be NON-DISRUPTIVE and can be run against
a live system. They perform read-only checks on:
- Service status
- Metadata integrity
- Config validation
- File system state
- Recent log analysis

Run with: pytest tests/test_health.py -v
Or use the CLI: python scripts/health_check.py
"""

import json
import os
import subprocess
from pathlib import Path

import pytest


# Default paths - can be overridden with environment variables
CACHE_DIR = Path(os.environ.get("PHOTOLOOP_CACHE", "/var/lib/photoloop/cache"))
CONFIG_PATH = Path(os.environ.get("PHOTOLOOP_CONFIG", "/etc/photoloop/config.yaml"))


class TestServiceHealth:
    """Test service is running correctly."""

    def test_service_is_active(self):
        """PhotoLoop service should be running."""
        result = subprocess.run(
            ["systemctl", "is-active", "photoloop"],
            capture_output=True,
            text=True
        )
        assert result.stdout.strip() == "active", "Service is not running"

    def test_service_not_failed(self):
        """Service should not be in failed state."""
        result = subprocess.run(
            ["systemctl", "is-failed", "photoloop"],
            capture_output=True,
            text=True
        )
        assert result.stdout.strip() != "failed", "Service is in failed state"

    def test_no_recent_crashes(self):
        """No service restarts in last 5 minutes."""
        result = subprocess.run(
            ["journalctl", "-u", "photoloop", "--since", "5 minutes ago",
             "--no-pager", "-o", "cat"],
            capture_output=True,
            text=True
        )

        crash_indicators = [
            "Deactivated successfully",  # Normal stop is ok
            "segfault",
            "killed",
            "core dumped"
        ]

        log = result.stdout.lower()
        for indicator in crash_indicators[1:]:  # Skip normal stop
            assert indicator not in log, f"Found crash indicator: {indicator}"


class TestMetadataIntegrity:
    """Test cache metadata integrity."""

    @pytest.fixture
    def metadata(self):
        """Load metadata from cache."""
        meta_path = CACHE_DIR / "metadata.json"
        if not meta_path.exists():
            pytest.skip("No metadata file found")

        with open(meta_path) as f:
            return json.load(f)

    def test_metadata_is_valid_json(self):
        """Metadata file should be valid JSON."""
        meta_path = CACHE_DIR / "metadata.json"
        if not meta_path.exists():
            pytest.skip("No metadata file found")

        with open(meta_path) as f:
            data = json.load(f)  # Should not raise

        assert "media" in data
        assert "settings" in data

    def test_no_missing_files(self, metadata):
        """All files in metadata should exist on disk."""
        missing = []

        for media_id, item in metadata["media"].items():
            local_path = item.get("local_path")
            if local_path and not item.get("deleted"):
                if not os.path.exists(local_path):
                    missing.append(local_path)

        assert len(missing) == 0, f"Missing files: {missing[:5]}..."

    def test_no_orphaned_files(self, metadata):
        """All jpg files should be in metadata."""
        if not CACHE_DIR.exists():
            pytest.skip("Cache directory not found")

        files_on_disk = set(f.name for f in CACHE_DIR.glob("*.jpg"))
        files_in_meta = set(
            os.path.basename(m["local_path"])
            for m in metadata["media"].values()
        )

        orphaned = files_on_disk - files_in_meta

        # Allow some tolerance (recently downloaded files)
        assert len(orphaned) <= 5, f"Too many orphaned files: {len(orphaned)}"

    def test_metadata_has_required_fields(self, metadata):
        """Each media item should have required fields."""
        required = ["media_id", "url", "local_path", "media_type"]

        for media_id, item in metadata["media"].items():
            for field in required:
                assert field in item, f"Missing {field} in {media_id}"

    def test_active_photos_count(self, metadata):
        """Should have some active (non-deleted) photos."""
        active = sum(
            1 for m in metadata["media"].values()
            if not m.get("deleted")
        )

        assert active > 0, "No active photos in cache"


class TestConfigHealth:
    """Test configuration health."""

    def test_config_exists(self):
        """Config file should exist."""
        assert CONFIG_PATH.exists(), f"Config not found at {CONFIG_PATH}"

    def test_config_is_valid(self):
        """Config should pass validation."""
        if not CONFIG_PATH.exists():
            pytest.skip("Config file not found")

        from src.config import load_config, validate_config

        config = load_config(str(CONFIG_PATH))
        errors = validate_config(config)

        assert len(errors) == 0, f"Config errors: {errors}"

    def test_album_url_not_placeholder(self):
        """Album URL should not be placeholder."""
        if not CONFIG_PATH.exists():
            pytest.skip("Config file not found")

        from src.config import load_config

        config = load_config(str(CONFIG_PATH))

        for album in config.albums:
            if album.url:
                assert "YOUR_ALBUM_URL_HERE" not in album.url, \
                    "Album URL is still placeholder"


class TestLogHealth:
    """Test recent logs for issues."""

    def test_no_recent_errors(self):
        """No ERROR level logs in last 10 minutes."""
        result = subprocess.run(
            ["journalctl", "-u", "photoloop", "--since", "10 minutes ago",
             "--no-pager", "-o", "cat", "-p", "err"],
            capture_output=True,
            text=True
        )

        # Filter out expected/transient errors
        allowed_errors = [
            "Timeout waiting for photo grid",  # Transient scraper issue
            "Connection refused",  # Chrome crash, handled by retry
        ]

        lines = result.stdout.strip().split('\n')
        real_errors = []
        for line in lines:
            if line and not any(allowed in line for allowed in allowed_errors):
                real_errors.append(line)

        assert len(real_errors) == 0, f"Found errors: {real_errors[:3]}"

    def test_photos_are_cycling(self):
        """Photos should be changing in slideshow."""
        result = subprocess.run(
            ["journalctl", "-u", "photoloop", "--since", "2 minutes ago",
             "--no-pager", "-o", "cat"],
            capture_output=True,
            text=True
        )

        display_count = result.stdout.count("Displaying photo:")

        # With 5-second duration, should see at least a few changes
        assert display_count >= 2, "Photos don't seem to be cycling"


class TestResourceHealth:
    """Test system resource usage."""

    def test_memory_usage_reasonable(self):
        """PhotoLoop should not use excessive memory."""
        # First get the PID
        pid_result = subprocess.run(
            ["pgrep", "-f", "photoloop.src.main"],
            capture_output=True,
            text=True
        )

        if not pid_result.stdout.strip():
            pytest.skip("Could not find PhotoLoop process")

        pid = pid_result.stdout.strip().split('\n')[0]

        # Then get memory usage
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", pid],
            capture_output=True,
            text=True
        )

        if not result.stdout.strip():
            pytest.skip("Could not get memory usage")

        # RSS in KB
        rss_kb = int(result.stdout.strip())
        rss_mb = rss_kb / 1024

        # Should be under 1GB (Chrome uses a lot of memory when loaded)
        assert rss_mb < 1000, f"Memory usage too high: {rss_mb:.0f} MB"

    def test_cache_size_within_limit(self):
        """Cache should not exceed configured limit."""
        if not CACHE_DIR.exists():
            pytest.skip("Cache directory not found")
        if not CONFIG_PATH.exists():
            pytest.skip("Config file not found")

        from src.config import load_config

        config = load_config(str(CONFIG_PATH))
        max_size_bytes = config.cache.max_size_mb * 1024 * 1024

        total_size = sum(
            f.stat().st_size for f in CACHE_DIR.glob("*.jpg")
        )

        # Allow 10% tolerance
        assert total_size < max_size_bytes * 1.1, \
            f"Cache size {total_size / 1024 / 1024:.0f} MB exceeds limit"
