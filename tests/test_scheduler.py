# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Tests for scheduler functionality including temporary overrides.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock


class TestSchedulerState:
    """Test basic scheduler state management."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config for scheduler."""
        config = MagicMock()
        config.schedule.enabled = True
        config.schedule.off_hours_mode = "black"
        config.schedule.weekday.start_time = "07:00"
        config.schedule.weekday.end_time = "22:00"
        config.schedule.weekend.start_time = "08:00"
        config.schedule.weekend.end_time = "23:00"
        config.schedule.overrides = {}
        return config

    @pytest.fixture
    def scheduler(self, mock_config):
        """Create a scheduler instance."""
        from src.scheduler import Scheduler
        return Scheduler(mock_config)

    def test_active_during_scheduled_hours(self, scheduler):
        """Test that scheduler reports active during scheduled hours."""
        from src.scheduler import ScheduleState
        # Monday at 10:00 (within 07:00-22:00)
        now = datetime(2025, 1, 6, 10, 0)  # Monday
        state = scheduler.get_current_state(now)
        assert state == ScheduleState.ACTIVE

    def test_off_hours_outside_schedule(self, scheduler):
        """Test that scheduler reports off-hours outside scheduled hours."""
        from src.scheduler import ScheduleState
        # Monday at 23:00 (outside 07:00-22:00)
        now = datetime(2025, 1, 6, 23, 0)  # Monday
        state = scheduler.get_current_state(now)
        assert state == ScheduleState.OFF_HOURS

    def test_force_on_override(self, scheduler):
        """Test force_on creates a permanent override."""
        from src.scheduler import ScheduleState
        # During off-hours
        now = datetime(2025, 1, 6, 23, 0)
        scheduler.force_on()
        state = scheduler.get_current_state(now)
        assert state == ScheduleState.FORCE_ON
        assert scheduler.has_override()

    def test_force_off_override(self, scheduler):
        """Test force_off creates a permanent override."""
        from src.scheduler import ScheduleState
        # During active hours
        now = datetime(2025, 1, 6, 10, 0)
        scheduler.force_off()
        state = scheduler.get_current_state(now)
        assert state == ScheduleState.FORCE_OFF
        assert scheduler.has_override()

    def test_clear_override(self, scheduler):
        """Test that clear_override removes overrides."""
        from src.scheduler import ScheduleState
        scheduler.force_on()
        assert scheduler.has_override()
        scheduler.clear_override()
        assert not scheduler.has_override()


class TestTemporaryOverride:
    """Test temporary override functionality."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config for scheduler."""
        config = MagicMock()
        config.schedule.enabled = True
        config.schedule.off_hours_mode = "black"
        config.schedule.weekday.start_time = "07:00"
        config.schedule.weekday.end_time = "22:00"
        config.schedule.weekend.start_time = "08:00"
        config.schedule.weekend.end_time = "23:00"
        config.schedule.overrides = {}
        return config

    @pytest.fixture
    def scheduler(self, mock_config):
        """Create a scheduler instance."""
        from src.scheduler import Scheduler
        return Scheduler(mock_config)

    def test_temporary_override_sets_until_time(self, scheduler):
        """Test that temporary override calculates correct end time."""
        # Monday at 23:00 (off-hours), end time is 22:00
        # Next end time should be Tuesday 22:00
        now = datetime(2025, 1, 6, 23, 0)  # Monday 11pm
        until = scheduler.force_on_temporarily(now)

        assert until is not None
        assert until.hour == 22
        assert until.minute == 0
        # Should be next day (Tuesday)
        assert until.day == 7

    def test_temporary_override_today_end_time(self, scheduler):
        """Test temporary override uses today's end time if before it."""
        # Monday at 10:00 (active hours), end time is 22:00
        now = datetime(2025, 1, 6, 10, 0)  # Monday 10am
        until = scheduler.force_on_temporarily(now)

        assert until is not None
        assert until.hour == 22
        assert until.minute == 0
        # Should be same day
        assert until.day == 6

    def test_temporary_override_expires(self, scheduler):
        """Test that temporary override auto-expires."""
        from src.scheduler import ScheduleState

        # Set temporary override at 23:00 (off-hours)
        start_time = datetime(2025, 1, 6, 23, 0)
        until = scheduler.force_on_temporarily(start_time)

        # Check state before expiration
        before_expiry = datetime(2025, 1, 7, 21, 0)
        state = scheduler.get_current_state(before_expiry)
        assert state == ScheduleState.FORCE_ON
        assert scheduler.has_temporary_override()

        # Check state after expiration
        after_expiry = datetime(2025, 1, 7, 22, 30)
        state = scheduler.get_current_state(after_expiry)
        # Should have auto-cleared and returned to normal state
        assert not scheduler.has_temporary_override()
        assert state == ScheduleState.OFF_HOURS

    def test_temporary_override_clears_with_clear_override(self, scheduler):
        """Test that clear_override also clears temporary flag."""
        now = datetime(2025, 1, 6, 23, 0)
        scheduler.force_on_temporarily(now)

        assert scheduler.has_temporary_override()
        scheduler.clear_override()
        assert not scheduler.has_temporary_override()

    def test_get_next_transition_with_temporary_override(self, scheduler):
        """Test that get_next_transition returns override expiration."""
        now = datetime(2025, 1, 6, 23, 0)
        until = scheduler.force_on_temporarily(now)

        next_trans = scheduler.get_next_transition(now)
        assert next_trans is not None
        assert next_trans[0] == until
        assert "temporary" in next_trans[1].lower()


class TestSchedulerStatus:
    """Test scheduler status reporting."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config for scheduler."""
        config = MagicMock()
        config.schedule.enabled = True
        config.schedule.off_hours_mode = "black"
        config.schedule.weekday.start_time = "07:00"
        config.schedule.weekday.end_time = "22:00"
        config.schedule.weekend.start_time = "08:00"
        config.schedule.weekend.end_time = "23:00"
        config.schedule.overrides = {}
        return config

    @pytest.fixture
    def scheduler(self, mock_config):
        """Create a scheduler instance."""
        from src.scheduler import Scheduler
        return Scheduler(mock_config)

    def test_status_includes_temporary_override_info(self, scheduler):
        """Test that get_status includes temporary override information."""
        now = datetime(2025, 1, 6, 23, 0)
        scheduler.force_on_temporarily(now)

        status = scheduler.get_status(now)
        assert "has_temporary_override" in status
        assert status["has_temporary_override"] is True
        assert "temporary_override_until" in status
        assert status["temporary_override_until"] is not None

    def test_status_without_temporary_override(self, scheduler):
        """Test that get_status shows no temporary override normally."""
        now = datetime(2025, 1, 6, 10, 0)
        status = scheduler.get_status(now)

        assert status["has_temporary_override"] is False
        assert status["temporary_override_until"] is None
