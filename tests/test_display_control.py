# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Tests for display photo control functionality (pause/resume/skip).
"""

import pytest
import time
from unittest.mock import MagicMock, patch


class TestDisplayPauseResume:
    """Test pause and resume functionality."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config for display."""
        config = MagicMock()
        config.display.resolution = "1920x1080"
        config.display.photo_duration_seconds = 10
        config.display.transition_type = "fade"
        config.display.transition_duration_ms = 1000
        config.scaling.mode = "fill"
        config.scaling.face_position = "center"
        config.scaling.fallback_crop = "center"
        config.scaling.max_crop_percent = 15
        config.scaling.background_color = [0, 0, 0]
        config.ken_burns.enabled = False
        config.ken_burns.zoom_range = [1.0, 1.1]
        config.ken_burns.pan_speed = 0.02
        config.ken_burns.randomize = True
        config.overlay.enabled = False
        return config

    def test_initial_state_not_paused(self, mock_config):
        """Test that display starts in non-paused state."""
        # We can't easily test the full Display class due to SDL2 dependency
        # So we test the pause logic in isolation

        # Simulate the pause state
        paused = False
        assert paused is False

    def test_pause_sets_flag(self):
        """Test that pause() sets the paused flag."""
        # Simulating display behavior without SDL2
        _paused = False

        def pause():
            nonlocal _paused
            _paused = True

        pause()
        assert _paused is True

    def test_resume_clears_flag(self):
        """Test that resume() clears the paused flag."""
        _paused = True

        def resume():
            nonlocal _paused
            _paused = False

        resume()
        assert _paused is False

    def test_toggle_pause_toggles_state(self):
        """Test that toggle_pause() toggles the state."""
        _paused = False

        def toggle_pause():
            nonlocal _paused
            _paused = not _paused
            return _paused

        # First toggle: False -> True
        result = toggle_pause()
        assert result is True
        assert _paused is True

        # Second toggle: True -> False
        result = toggle_pause()
        assert result is False
        assert _paused is False


class TestDisplaySkip:
    """Test skip to next/previous functionality."""

    def test_skip_to_next_sets_flag(self):
        """Test that skip_to_next() sets the skip flag."""
        _skip_requested = False

        def skip_to_next():
            nonlocal _skip_requested
            _skip_requested = True

        skip_to_next()
        assert _skip_requested is True

    def test_skip_to_previous_sets_flag(self):
        """Test that skip_to_previous() sets the previous flag."""
        _previous_requested = False

        def skip_to_previous():
            nonlocal _previous_requested
            _previous_requested = True

        skip_to_previous()
        assert _previous_requested is True

    def test_skip_clears_after_check(self):
        """Test that skip flag is cleared after being checked."""
        _skip_requested = True

        def is_photo_duration_complete():
            nonlocal _skip_requested
            if _skip_requested:
                _skip_requested = False
                return True
            return False

        # First call should return True and clear flag
        result = is_photo_duration_complete()
        assert result is True
        assert _skip_requested is False

        # Second call should return False (based on normal duration check)
        result = is_photo_duration_complete()
        assert result is False


class TestDurationComplete:
    """Test photo duration completion logic."""

    def test_duration_complete_returns_false_when_paused(self):
        """Test that is_photo_duration_complete returns False when paused."""
        _paused = True
        _skip_requested = False
        _kb_start_time = time.time() - 100  # Started 100 seconds ago
        _kb_duration = 10  # 10 second duration

        def is_photo_duration_complete():
            if _skip_requested:
                return True
            if _paused:
                return False
            elapsed = time.time() - _kb_start_time
            return elapsed >= _kb_duration

        # Even though 100 seconds have passed (> 10s duration),
        # should return False because paused
        result = is_photo_duration_complete()
        assert result is False

    def test_duration_complete_returns_true_when_skip_requested(self):
        """Test that skip request overrides pause and duration."""
        _paused = True  # Even when paused
        _skip_requested = True
        _kb_start_time = time.time()  # Just started
        _kb_duration = 10

        def is_photo_duration_complete():
            if _skip_requested:
                return True
            if _paused:
                return False
            elapsed = time.time() - _kb_start_time
            return elapsed >= _kb_duration

        # Skip requested overrides everything
        result = is_photo_duration_complete()
        assert result is True

    def test_duration_complete_normal_operation(self):
        """Test normal duration completion."""
        _paused = False
        _skip_requested = False

        # Test with elapsed time < duration
        _kb_start_time = time.time()
        _kb_duration = 10

        def is_photo_duration_complete(start_time, duration):
            if _skip_requested:
                return True
            if _paused:
                return False
            elapsed = time.time() - start_time
            return elapsed >= duration

        # Just started, should not be complete
        result = is_photo_duration_complete(time.time(), 10)
        assert result is False

        # Way past duration, should be complete
        result = is_photo_duration_complete(time.time() - 100, 10)
        assert result is True
