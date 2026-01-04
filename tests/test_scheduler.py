# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
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
        from src.config import ScheduleEvent

        config = MagicMock()
        config.schedule.enabled = True
        config.schedule.off_hours_mode = "black"
        config.schedule.default_screensaver_mode = "black"
        config.schedule.weekday.start_time = "07:00"
        config.schedule.weekday.end_time = "22:00"
        config.schedule.weekend.start_time = "08:00"
        config.schedule.weekend.end_time = "23:00"
        config.schedule.overrides = {}

        # Mock holidays config
        config.schedule.holidays = MagicMock()
        config.schedule.holidays.use_weekend_schedule = False
        config.schedule.holidays.countries = []

        # Mock get_events_for_day_type to return proper events
        def get_events(is_weekend):
            if is_weekend:
                return [
                    ScheduleEvent(start_time="00:00", end_time="08:00", mode="black"),
                    ScheduleEvent(start_time="08:00", end_time="23:00", mode="slideshow"),
                    ScheduleEvent(start_time="23:00", end_time="24:00", mode="black"),
                ]
            else:
                return [
                    ScheduleEvent(start_time="00:00", end_time="07:00", mode="black"),
                    ScheduleEvent(start_time="07:00", end_time="22:00", mode="slideshow"),
                    ScheduleEvent(start_time="22:00", end_time="24:00", mode="black"),
                ]
        config.schedule.get_events_for_day_type = get_events
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
        from src.config import ScheduleEvent

        config = MagicMock()
        config.schedule.enabled = True
        config.schedule.off_hours_mode = "black"
        config.schedule.default_screensaver_mode = "black"
        config.schedule.weekday.start_time = "07:00"
        config.schedule.weekday.end_time = "22:00"
        config.schedule.weekend.start_time = "08:00"
        config.schedule.weekend.end_time = "23:00"
        config.schedule.overrides = {}

        # Mock holidays config
        config.schedule.holidays = MagicMock()
        config.schedule.holidays.use_weekend_schedule = False
        config.schedule.holidays.countries = []

        # Mock get_events_for_day_type to return proper events
        def get_events(is_weekend):
            if is_weekend:
                return [
                    ScheduleEvent(start_time="00:00", end_time="08:00", mode="black"),
                    ScheduleEvent(start_time="08:00", end_time="23:00", mode="slideshow"),
                    ScheduleEvent(start_time="23:00", end_time="24:00", mode="black"),
                ]
            else:
                return [
                    ScheduleEvent(start_time="00:00", end_time="07:00", mode="black"),
                    ScheduleEvent(start_time="07:00", end_time="22:00", mode="slideshow"),
                    ScheduleEvent(start_time="22:00", end_time="24:00", mode="black"),
                ]
        config.schedule.get_events_for_day_type = get_events
        return config

    @pytest.fixture
    def scheduler(self, mock_config):
        """Create a scheduler instance."""
        from src.scheduler import Scheduler
        return Scheduler(mock_config)

    def test_force_on_sets_expiry(self, scheduler):
        """Test that force_on sets an expiration time."""
        scheduler.force_on()
        expiry = scheduler.get_override_expiry()
        # Override should expire at next event start
        assert expiry is not None

    def test_force_off_sets_expiry(self, scheduler):
        """Test that force_off sets an expiration time."""
        scheduler.force_off()
        expiry = scheduler.get_override_expiry()
        # Override should expire at next event start
        assert expiry is not None

    def test_override_expires_automatically(self, scheduler):
        """Test that override auto-expires when checked after expiry time."""
        from src.scheduler import ScheduleState

        # Set override
        scheduler.force_on()
        expiry = scheduler.get_override_expiry()
        assert expiry is not None
        assert scheduler.has_override()

        # Check state before expiration - should still have override
        before_expiry = expiry - timedelta(minutes=30)
        state = scheduler.get_current_state(before_expiry)
        assert state == ScheduleState.FORCE_ON
        assert scheduler.has_override()

        # Check state after expiration - override should auto-clear
        after_expiry = expiry + timedelta(minutes=30)
        state = scheduler.get_current_state(after_expiry)
        # Should have auto-cleared and returned to normal state
        assert not scheduler.has_override()

    def test_clear_override_clears_expiry(self, scheduler):
        """Test that clear_override removes the expiration time."""
        scheduler.force_on()
        assert scheduler.has_override()
        assert scheduler.get_override_expiry() is not None

        scheduler.clear_override()
        assert not scheduler.has_override()
        assert scheduler.get_override_expiry() is None

    def test_get_next_transition_with_override(self, scheduler):
        """Test that get_next_transition returns override expiration when active."""
        scheduler.force_on()
        expiry = scheduler.get_override_expiry()

        next_trans = scheduler.get_next_transition()
        assert next_trans is not None
        assert next_trans[0] == expiry
        assert "override" in next_trans[1].lower() or "expire" in next_trans[1].lower()


class TestSchedulerStatus:
    """Test scheduler status reporting."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock config for scheduler."""
        from src.config import ScheduleEvent

        config = MagicMock()
        config.schedule.enabled = True
        config.schedule.off_hours_mode = "black"
        config.schedule.default_screensaver_mode = "black"
        config.schedule.weekday.start_time = "07:00"
        config.schedule.weekday.end_time = "22:00"
        config.schedule.weekend.start_time = "08:00"
        config.schedule.weekend.end_time = "23:00"
        config.schedule.overrides = {}

        # Mock holidays config
        config.schedule.holidays = MagicMock()
        config.schedule.holidays.use_weekend_schedule = False
        config.schedule.holidays.countries = []

        # Mock get_events_for_day_type to return proper events
        def get_events(is_weekend):
            if is_weekend:
                return [
                    ScheduleEvent(start_time="00:00", end_time="08:00", mode="black"),
                    ScheduleEvent(start_time="08:00", end_time="23:00", mode="slideshow"),
                    ScheduleEvent(start_time="23:00", end_time="24:00", mode="black"),
                ]
            else:
                return [
                    ScheduleEvent(start_time="00:00", end_time="07:00", mode="black"),
                    ScheduleEvent(start_time="07:00", end_time="22:00", mode="slideshow"),
                    ScheduleEvent(start_time="22:00", end_time="24:00", mode="black"),
                ]
        config.schedule.get_events_for_day_type = get_events
        return config

    @pytest.fixture
    def scheduler(self, mock_config):
        """Create a scheduler instance."""
        from src.scheduler import Scheduler
        return Scheduler(mock_config)

    def test_status_includes_override_info(self, scheduler):
        """Test that get_status includes override information when active."""
        scheduler.force_on()
        now = datetime(2025, 1, 6, 23, 0)

        status = scheduler.get_status(now)
        assert "has_override" in status
        assert status["has_override"] is True
        assert "override" in status
        assert status["override"] is not None
        assert status["override"]["type"] == "force_on"
        assert status["override"]["expires"] is not None

    def test_status_without_override(self, scheduler):
        """Test that get_status shows no override when none is set."""
        now = datetime(2025, 1, 6, 10, 0)
        status = scheduler.get_status(now)

        assert status["has_override"] is False
        assert status["override"] is None
