# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Scheduler for PhotoLoop.
Handles time-based scheduling with weekday/weekend support and manual overrides.
"""

import logging
from datetime import datetime, time as dt_time
from enum import Enum
from typing import Optional, Tuple

from .config import PhotoLoopConfig, ScheduleConfig, ScheduleTimeConfig

logger = logging.getLogger(__name__)


class ScheduleState(Enum):
    """Current schedule state."""
    ACTIVE = "active"           # Within scheduled hours
    OFF_HOURS = "off_hours"     # Outside scheduled hours
    FORCE_ON = "force_on"       # Manual override - on
    FORCE_OFF = "force_off"     # Manual override - off


class Scheduler:
    """
    Manages time-based scheduling for the slideshow.

    Features:
    - Separate weekday/weekend schedules
    - Per-day overrides
    - Manual override support
    - Next transition time calculation
    """

    # Day name mapping (0 = Monday, 6 = Sunday)
    DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    def __init__(self, config: PhotoLoopConfig):
        """
        Initialize the scheduler.

        Args:
            config: PhotoLoop configuration.
        """
        self.config = config
        self._override: Optional[ScheduleState] = None

    def _parse_time(self, time_str: str) -> dt_time:
        """
        Parse a time string in HH:MM format.

        Args:
            time_str: Time string like "07:00" or "22:30".

        Returns:
            datetime.time object.
        """
        parts = time_str.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return dt_time(hour=hour, minute=minute)

    def _get_schedule_for_day(self, day: int) -> ScheduleTimeConfig:
        """
        Get the schedule configuration for a specific day.

        Args:
            day: Day of week (0 = Monday, 6 = Sunday).

        Returns:
            ScheduleTimeConfig for that day.
        """
        schedule = self.config.schedule
        day_name = self.DAY_NAMES[day]

        # Check for day-specific override
        if schedule.overrides and day_name in schedule.overrides:
            return schedule.overrides[day_name]

        # Weekend (Saturday = 5, Sunday = 6)
        if day >= 5:
            return schedule.weekend

        # Weekday
        return schedule.weekday

    def _is_time_in_range(
        self,
        current: dt_time,
        start: dt_time,
        end: dt_time
    ) -> bool:
        """
        Check if a time is within a range.
        Handles overnight ranges (e.g., 22:00 to 06:00).

        Args:
            current: Time to check.
            start: Range start time.
            end: Range end time.

        Returns:
            True if current is within the range.
        """
        if start <= end:
            # Normal range (e.g., 07:00 to 22:00)
            return start <= current <= end
        else:
            # Overnight range (e.g., 22:00 to 06:00)
            return current >= start or current <= end

    def get_current_state(self, now: Optional[datetime] = None) -> ScheduleState:
        """
        Get the current schedule state.

        Args:
            now: Current datetime (defaults to now).

        Returns:
            ScheduleState indicating what should be displayed.
        """
        if now is None:
            now = datetime.now()

        # Check for manual override
        if self._override is not None:
            return self._override

        # If scheduling disabled, always active
        if not self.config.schedule.enabled:
            return ScheduleState.ACTIVE

        # Get schedule for today
        day = now.weekday()
        schedule = self._get_schedule_for_day(day)

        start = self._parse_time(schedule.start_time)
        end = self._parse_time(schedule.end_time)
        current = now.time()

        if self._is_time_in_range(current, start, end):
            return ScheduleState.ACTIVE
        else:
            return ScheduleState.OFF_HOURS

    def should_show_slideshow(self, now: Optional[datetime] = None) -> bool:
        """
        Check if the slideshow should be displayed.

        Args:
            now: Current datetime.

        Returns:
            True if slideshow should be shown.
        """
        state = self.get_current_state(now)
        return state in (ScheduleState.ACTIVE, ScheduleState.FORCE_ON)

    def get_off_hours_mode(self) -> str:
        """
        Get what to display during off-hours.

        Returns:
            "black" or "clock".
        """
        return self.config.schedule.off_hours_mode

    def force_on(self) -> None:
        """Force the slideshow on (manual override)."""
        self._override = ScheduleState.FORCE_ON
        logger.info("Schedule override: FORCE ON")

    def force_off(self) -> None:
        """Force the slideshow off (manual override)."""
        self._override = ScheduleState.FORCE_OFF
        logger.info("Schedule override: FORCE OFF")

    def clear_override(self) -> None:
        """Clear manual override and resume normal schedule."""
        self._override = None
        logger.info("Schedule override cleared, resuming normal schedule")

    def has_override(self) -> bool:
        """Check if there's an active manual override."""
        return self._override is not None

    def get_next_transition(self, now: Optional[datetime] = None) -> Optional[Tuple[datetime, str]]:
        """
        Get the next scheduled transition time.

        Args:
            now: Current datetime.

        Returns:
            Tuple of (datetime, description) or None if scheduling disabled.
        """
        if not self.config.schedule.enabled:
            return None

        if now is None:
            now = datetime.now()

        state = self.get_current_state(now)

        # If there's a manual override, no automatic transition
        if state in (ScheduleState.FORCE_ON, ScheduleState.FORCE_OFF):
            return None

        day = now.weekday()
        schedule = self._get_schedule_for_day(day)
        current_time = now.time()

        start = self._parse_time(schedule.start_time)
        end = self._parse_time(schedule.end_time)

        if state == ScheduleState.ACTIVE:
            # Currently active, next transition is to off-hours
            if current_time <= end:
                # End time is today
                next_dt = now.replace(
                    hour=end.hour,
                    minute=end.minute,
                    second=0,
                    microsecond=0
                )
                return (next_dt, "slideshow ends")
            else:
                # End time is tomorrow (overnight schedule)
                from datetime import timedelta
                tomorrow = now + timedelta(days=1)
                next_dt = tomorrow.replace(
                    hour=end.hour,
                    minute=end.minute,
                    second=0,
                    microsecond=0
                )
                return (next_dt, "slideshow ends")
        else:
            # Currently off-hours, next transition is to active
            if current_time < start:
                # Start time is today
                next_dt = now.replace(
                    hour=start.hour,
                    minute=start.minute,
                    second=0,
                    microsecond=0
                )
                return (next_dt, "slideshow starts")
            else:
                # Start time is tomorrow
                from datetime import timedelta
                tomorrow = now + timedelta(days=1)
                tomorrow_schedule = self._get_schedule_for_day(tomorrow.weekday())
                tomorrow_start = self._parse_time(tomorrow_schedule.start_time)
                next_dt = tomorrow.replace(
                    hour=tomorrow_start.hour,
                    minute=tomorrow_start.minute,
                    second=0,
                    microsecond=0
                )
                return (next_dt, "slideshow starts")

    def get_today_schedule(self, now: Optional[datetime] = None) -> dict:
        """
        Get today's schedule information.

        Args:
            now: Current datetime.

        Returns:
            Dict with schedule details.
        """
        if now is None:
            now = datetime.now()

        day = now.weekday()
        day_name = self.DAY_NAMES[day]
        schedule = self._get_schedule_for_day(day)

        is_weekend = day >= 5
        is_override = schedule == self.config.schedule.overrides.get(day_name)

        return {
            "day": day_name.capitalize(),
            "is_weekend": is_weekend,
            "is_override": is_override,
            "start_time": schedule.start_time,
            "end_time": schedule.end_time,
            "enabled": self.config.schedule.enabled
        }

    def get_status(self, now: Optional[datetime] = None) -> dict:
        """
        Get comprehensive schedule status.

        Args:
            now: Current datetime.

        Returns:
            Dict with full status information.
        """
        if now is None:
            now = datetime.now()

        state = self.get_current_state(now)
        next_transition = self.get_next_transition(now)
        today = self.get_today_schedule(now)

        return {
            "state": state.value,
            "should_show_slideshow": self.should_show_slideshow(now),
            "off_hours_mode": self.get_off_hours_mode(),
            "has_override": self.has_override(),
            "next_transition": {
                "time": next_transition[0].isoformat() if next_transition else None,
                "description": next_transition[1] if next_transition else None
            },
            "today": today,
            "current_time": now.isoformat()
        }
