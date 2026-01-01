# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""Tests for display module.

Note: Many display tests require pygame/SDL2 initialization which
may fail in headless CI environments. These are tested separately.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

from src.display import TransitionType, DisplayMode
from src.cache_manager import CachedMedia
from src.config import PhotoLoopConfig, OverlayConfig


class TestTransitionType:
    """Tests for TransitionType enum."""

    def test_transition_values(self):
        """Test all transition type values."""
        assert TransitionType.NONE.value == "none"
        assert TransitionType.FADE.value == "fade"
        assert TransitionType.SLIDE_LEFT.value == "slide_left"
        assert TransitionType.SLIDE_RIGHT.value == "slide_right"
        assert TransitionType.SLIDE_UP.value == "slide_up"
        assert TransitionType.SLIDE_DOWN.value == "slide_down"

    def test_transition_from_string(self):
        """Test creating TransitionType from string."""
        assert TransitionType("fade") == TransitionType.FADE
        assert TransitionType("slide_left") == TransitionType.SLIDE_LEFT


class TestDisplayMode:
    """Tests for DisplayMode enum."""

    def test_display_mode_values(self):
        """Test all display mode values."""
        assert DisplayMode.SLIDESHOW.value == "slideshow"
        assert DisplayMode.BLACK.value == "black"
        assert DisplayMode.CLOCK.value == "clock"


class TestDisplayTextWrapping:
    """Test text wrapping functionality.

    We can't instantiate Display without pygame, so we test the algorithm directly.
    """

    def test_wrap_text_short(self):
        """Test that short text doesn't wrap."""
        # Simulating _wrap_text logic
        def wrap_text(text, max_width_chars=50):
            words = text.split()
            lines = []
            current_line = []
            current_length = 0

            for word in words:
                if current_length + len(word) + 1 <= max_width_chars:
                    current_line.append(word)
                    current_length += len(word) + 1
                else:
                    if current_line:
                        lines.append(" ".join(current_line))
                    current_line = [word]
                    current_length = len(word)

            if current_line:
                lines.append(" ".join(current_line))

            return lines

        # Short text - no wrapping needed
        result = wrap_text("Hello world")
        assert result == ["Hello world"]

    def test_wrap_text_long(self):
        """Test that long text wraps correctly."""
        def wrap_text(text, max_width_chars=50):
            words = text.split()
            lines = []
            current_line = []
            current_length = 0

            for word in words:
                if current_length + len(word) + 1 <= max_width_chars:
                    current_line.append(word)
                    current_length += len(word) + 1
                else:
                    if current_line:
                        lines.append(" ".join(current_line))
                    current_line = [word]
                    current_length = len(word)

            if current_line:
                lines.append(" ".join(current_line))

            return lines

        # Long text should wrap
        long_text = "This is a very long caption that should definitely wrap to multiple lines when displayed"
        result = wrap_text(long_text, max_width_chars=30)
        assert len(result) > 1
        for line in result:
            assert len(line) <= 35  # Allow some overflow for last word


class TestCaptionBuilding:
    """Test caption building logic."""

    def test_caption_source_priority(self):
        """Test that caption sources are prioritized correctly."""
        # Simulate the _build_caption logic
        source_priorities = {
            "google_caption": 1,
            "embedded_caption": 2,
            "google_location": 3,
            "exif_location": 4
        }

        available = [
            (source_priorities["google_location"], "Paris, France"),
            (source_priorities["google_caption"], "My vacation photo"),
            (source_priorities["exif_location"], "France"),
        ]

        # Sort by priority (lower number = higher priority)
        available.sort(key=lambda x: x[0])

        # Take top 1 source
        selected = [value for _, value in available[:1]]
        result = " — ".join(selected)

        assert result == "My vacation photo"  # google_caption has priority 1

    def test_caption_filters_invalid(self):
        """Test that invalid captions are filtered."""
        invalid_values = {'unknown location', 'add location', 'add a description'}

        test_values = [
            ("Valid caption", True),
            ("unknown location", False),
            ("Add Location", False),  # Case insensitive
            ("ADD A DESCRIPTION", False),
        ]

        for value, should_include in test_values:
            result = value.lower() not in invalid_values
            assert result == should_include, f"Failed for '{value}'"

    def test_camera_info_filtered(self):
        """Test that camera info strings are filtered from captions."""
        camera_info_patterns = [
            'DIGITAL CAMERA', 'DIGITAL PHOTO', 'CAMERA PHONE',
            'OLYMPUS', 'FUJIFILM', 'FUJI', 'CANON', 'NIKON', 'SONY',
            'SAMSUNG', 'PANASONIC', 'KODAK', 'LEICA', 'PENTAX', 'RICOH',
        ]

        def is_camera_info(caption):
            cap_upper = caption.upper().strip()
            for pattern in camera_info_patterns:
                if pattern in cap_upper:
                    return True
            return False

        assert is_camera_info("OLYMPUS DIGITAL CAMERA") is True
        assert is_camera_info("Canon EOS R5") is True
        assert is_camera_info("Family vacation 2024") is False
        assert is_camera_info("Birthday party at Nikon park") is True  # Contains NIKON

    def test_duplicate_captions_deduplicated(self):
        """Test that duplicate caption values are skipped."""
        # Simulate the deduplication logic from _build_caption
        available = []
        seen_values = set()

        def add_if_unique(priority, value):
            normalized = value.lower().strip()
            if normalized not in seen_values:
                seen_values.add(normalized)
                available.append((priority, value))

        # Add same caption from two sources
        add_if_unique(1, "Beach sunset")
        add_if_unique(2, "Beach sunset")  # Duplicate - should be skipped
        add_if_unique(3, "Hawaii")

        # Sort by priority
        available.sort(key=lambda x: x[0])

        # Take top 2
        selected = [value for _, value in available[:2]]
        result = " — ".join(selected)

        # Should be "Beach sunset — Hawaii", not "Beach sunset — Beach sunset"
        assert result == "Beach sunset — Hawaii"
        assert len(available) == 2  # Only 2 unique values

    def test_duplicate_captions_case_insensitive(self):
        """Test that duplicate detection is case-insensitive."""
        available = []
        seen_values = set()

        def add_if_unique(priority, value):
            normalized = value.lower().strip()
            if normalized not in seen_values:
                seen_values.add(normalized)
                available.append((priority, value))

        add_if_unique(1, "Beach Sunset")
        add_if_unique(2, "beach sunset")  # Same, different case - should be skipped
        add_if_unique(3, "BEACH SUNSET")  # Same, different case - should be skipped

        assert len(available) == 1
        assert available[0][1] == "Beach Sunset"  # Preserves original case


class TestEaseInOut:
    """Test easing function."""

    def test_ease_boundaries(self):
        """Test ease function at boundaries."""
        def ease_in_out(t):
            if t < 0.5:
                return 2 * t * t
            else:
                return 1 - pow(-2 * t + 2, 2) / 2

        # At t=0, should be 0
        assert ease_in_out(0.0) == 0.0

        # At t=1, should be 1
        assert ease_in_out(1.0) == 1.0

        # At t=0.5, should be 0.5
        assert ease_in_out(0.5) == 0.5

    def test_ease_symmetry(self):
        """Test that easing is symmetric around midpoint."""
        def ease_in_out(t):
            if t < 0.5:
                return 2 * t * t
            else:
                return 1 - pow(-2 * t + 2, 2) / 2

        # Check symmetry: ease(0.25) + ease(0.75) should equal 1
        assert abs(ease_in_out(0.25) + ease_in_out(0.75) - 1.0) < 0.001


class TestCachedMediaIntegration:
    """Test Display integration with CachedMedia."""

    def test_cached_media_has_required_fields(self):
        """Test that CachedMedia has fields Display expects."""
        media = CachedMedia(
            media_id="test123",
            url="https://example.com/photo.jpg",
            local_path="/path/to/photo.jpg",
            media_type="photo"
        )

        # Fields Display._build_caption expects
        assert hasattr(media, 'google_caption')
        assert hasattr(media, 'embedded_caption')
        assert hasattr(media, 'google_location')
        assert hasattr(media, 'location')

        # Fields Display._render_overlay expects
        assert hasattr(media, 'exif_date')
        assert hasattr(media, 'google_date')

        # Fields for lazy geocoding
        assert hasattr(media, 'gps_latitude')
        assert hasattr(media, 'gps_longitude')
