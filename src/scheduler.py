# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""
Scheduler for PhotoLoop.
Handles time-based scheduling with event-based scheduling, holiday awareness,
and auto-expiring manual overrides.
"""

import logging
from datetime import datetime, time as dt_time, timedelta
from enum import Enum
from typing import Optional, Tuple, List

from .config import PhotoLoopConfig, ScheduleConfig, ScheduleTimeConfig, ScheduleEvent

logger = logging.getLogger(__name__)

# Try to import holidays library for holiday detection
try:
    import holidays as holidays_lib
    HOLIDAYS_AVAILABLE = True
except ImportError:
    HOLIDAYS_AVAILABLE = False
    logger.warning("holidays library not installed - holiday detection disabled")


class ScheduleState(Enum):
    """Current schedule state."""
    ACTIVE = "active"           # Scheduled slideshow time (legacy compatibility)
    OFF_HOURS = "off_hours"     # Outside scheduled hours (legacy compatibility)
    SLIDESHOW = "slideshow"     # Event-based: show slideshow
    CLOCK = "clock"             # Event-based: show clock display
    BLACK = "black"             # Event-based: display off (DPMS standby)
    FORCE_ON = "force_on"       # Manual override - slideshow on
    FORCE_OFF = "force_off"     # Manual override - off (uses default_screensaver_mode)


class Scheduler:
    """
    Manages time-based scheduling for the slideshow.

    Features:
    - Event-based scheduling (multiple modes per day)
    - Separate weekday/weekend schedules
    - Holiday-aware scheduling (use weekend schedule on holidays)
    - Auto-expiring manual overrides
    - Per-day overrides (legacy)
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
        self._override_expires: Optional[datetime] = None
        self._override_mode: Optional[str] = None  # Specific mode for FORCE_OFF
        self._holiday_cache: dict = {}  # Cache holiday lookups

    def _parse_time(self, time_str: str | int) -> dt_time:
        """
        Parse a time string in HH:MM format, or an integer (YAML sexagesimal).

        Args:
            time_str: Time string like "07:00" or "22:30" or "24:00",
                      or an integer from YAML sexagesimal parsing (e.g., 480 for 08:00).

        Returns:
            datetime.time object.
        """
        # Handle YAML sexagesimal parsing (e.g., 08:00 becomes 480)
        if isinstance(time_str, int):
            # YAML parses HH:MM as hours*60 + minutes
            hour = time_str // 60
            minute = time_str % 60
        else:
            parts = time_str.strip().split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0

        # Handle "24:00" as end of day (midnight)
        if hour == 24:
            hour = 23
            minute = 59

        return dt_time(hour=hour, minute=minute)

    def _time_to_minutes(self, t: dt_time) -> int:
        """Convert time to minutes since midnight."""
        return t.hour * 60 + t.minute

    def _is_today_holiday(self, date: datetime) -> bool:
        """
        Check if the given date is a holiday in any configured country.

        Args:
            date: Date to check.

        Returns:
            True if it's a holiday in any configured country.
        """
        if not HOLIDAYS_AVAILABLE:
            return False

        holidays_config = self.config.schedule.holidays
        if not holidays_config.use_weekend_schedule or not holidays_config.countries:
            return False

        # Cache key based on date and countries
        cache_key = (date.date(), tuple(sorted(holidays_config.countries)))
        if cache_key in self._holiday_cache:
            return self._holiday_cache[cache_key]

        # Check each configured country
        is_holiday = False
        for country_code in holidays_config.countries:
            try:
                country_holidays = holidays_lib.country_holidays(country_code, years=date.year)
                if date.date() in country_holidays:
                    is_holiday = True
                    logger.debug(f"Holiday detected: {country_holidays.get(date.date())} ({country_code})")
                    break
            except Exception as e:
                logger.warning(f"Failed to check holidays for {country_code}: {e}")

        self._holiday_cache[cache_key] = is_holiday
        return is_holiday

    def _get_schedule_for_day(self, day: int) -> ScheduleTimeConfig:
        """
        Get the schedule configuration for a specific day (legacy format).

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

    def _get_events_for_day(self, now: datetime) -> List[ScheduleEvent]:
        """
        Get the schedule events for the given day.

        Takes into account:
        - Weekday vs weekend
        - Holidays (use weekend schedule if configured)

        Args:
            now: The datetime to get events for.

        Returns:
            List of ScheduleEvent for that day.
        """
        day = now.weekday()
        is_weekend = day >= 5

        # Check if today is a holiday (and should use weekend schedule)
        if not is_weekend and self._is_today_holiday(now):
            logger.debug("Using weekend schedule for holiday")
            is_weekend = True

        return self.config.schedule.get_events_for_day_type(is_weekend)

    def _get_current_event(self, now: datetime) -> Optional[ScheduleEvent]:
        """
        Find the event that covers the current time.

        Args:
            now: Current datetime.

        Returns:
            The ScheduleEvent covering the current time, or None.
        """
        events = self._get_events_for_day(now)
        current_time = now.time()
        current_minutes = self._time_to_minutes(current_time)

        for event in events:
            start = self._parse_time(event.start_time)
            end = self._parse_time(event.end_time)
            start_minutes = self._time_to_minutes(start)
            end_minutes = self._time_to_minutes(end)

            # Handle "24:00" / end of day
            if event.end_time == "24:00":
                end_minutes = 24 * 60

            if start_minutes <= current_minutes < end_minutes:
                return event

        # Fallback: return first event if no match (shouldn't happen with proper config)
        return events[0] if events else None

    def _get_next_event_start(self, now: datetime) -> Optional[datetime]:
        """
        Get the start time of the next event.

        Used for calculating override expiry.

        Args:
            now: Current datetime.

        Returns:
            Datetime of next event start, or None.
        """
        events = self._get_events_for_day(now)
        current_time = now.time()
        current_minutes = self._time_to_minutes(current_time)

        # Find next event today
        for event in events:
            start = self._parse_time(event.start_time)
            start_minutes = self._time_to_minutes(start)

            if start_minutes > current_minutes:
                return now.replace(
                    hour=start.hour,
                    minute=start.minute,
                    second=0,
                    microsecond=0
                )

        # Next event is tomorrow's first event
        tomorrow = now + timedelta(days=1)
        tomorrow_events = self._get_events_for_day(tomorrow)
        if tomorrow_events:
            first_start = self._parse_time(tomorrow_events[0].start_time)
            return tomorrow.replace(
                hour=first_start.hour,
                minute=first_start.minute,
                second=0,
                microsecond=0
            )

        return None

    def _check_override_expiry(self, now: datetime) -> None:
        """
        Check if manual override has expired and clear it if so.

        Args:
            now: Current datetime.
        """
        if self._override is not None and self._override_expires is not None:
            if now >= self._override_expires:
                logger.info(f"Override expired at {self._override_expires}, resuming schedule")
                self._override = None
                self._override_expires = None
                self._override_mode = None

    def _is_time_in_range(
        self,
        current: dt_time,
        start: dt_time,
        end: dt_time
    ) -> bool:
        """
        Check if a time is within a range (legacy method).
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

    def get_display_mode(self, now: Optional[datetime] = None) -> str:
        """
        Get what should be displayed right now.

        This is the main method for determining display behavior with event-based
        scheduling. Returns the mode string directly usable by display code.

        Args:
            now: Current datetime (defaults to now).

        Returns:
            One of: "slideshow", "clock", "black"
        """
        if now is None:
            now = datetime.now()

        # Check and clear expired override
        self._check_override_expiry(now)

        # Handle manual overrides
        if self._override == ScheduleState.FORCE_ON:
            return "slideshow"
        elif self._override == ScheduleState.FORCE_OFF:
            # Use specific override mode if set, otherwise fall back to default
            return self._override_mode or self.config.schedule.default_screensaver_mode

        # If scheduling disabled, always slideshow
        if not self.config.schedule.enabled:
            return "slideshow"

        # Get current event
        event = self._get_current_event(now)
        if event:
            return event.mode

        # Fallback to black if no event found
        return "black"

    def get_current_state(self, now: Optional[datetime] = None) -> ScheduleState:
        """
        Get the current schedule state.

        For backward compatibility with existing code. Maps event-based
        modes to legacy states.

        Args:
            now: Current datetime (defaults to now).

        Returns:
            ScheduleState indicating what should be displayed.
        """
        if now is None:
            now = datetime.now()

        # Check and clear expired override
        self._check_override_expiry(now)

        # Check for manual override
        if self._override is not None:
            return self._override

        # If scheduling disabled, always active
        if not self.config.schedule.enabled:
            return ScheduleState.ACTIVE

        # Get display mode and map to state
        mode = self.get_display_mode(now)
        if mode == "slideshow":
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
        return self.get_display_mode(now) == "slideshow"

    def get_off_hours_mode(self) -> str:
        """
        Get what to display during off-hours (legacy method).

        Returns:
            "black" or "clock".
        """
        return self.config.schedule.off_hours_mode

    def force_on(self) -> None:
        """
        Force the slideshow on (manual override).

        Override expires at the next scheduled event start time.
        """
        self._override = ScheduleState.FORCE_ON
        self._override_expires = self._get_next_event_start(datetime.now())
        if self._override_expires:
            logger.info(f"Schedule override: FORCE ON (expires at {self._override_expires})")
        else:
            logger.info("Schedule override: FORCE ON (no expiry)")

    def force_off(self) -> None:
        """
        Force the slideshow off (manual override).

        Override expires at the next scheduled event start time.
        Uses default_screensaver_mode for what to display.
        """
        self._override = ScheduleState.FORCE_OFF
        self._override_expires = self._get_next_event_start(datetime.now())
        self._override_mode = None  # Use default_screensaver_mode
        mode = self.config.schedule.default_screensaver_mode
        if self._override_expires:
            logger.info(f"Schedule override: FORCE OFF ({mode}) (expires at {self._override_expires})")
        else:
            logger.info(f"Schedule override: FORCE OFF ({mode}) (no expiry)")

    def force_mode(self, mode: str) -> None:
        """
        Force a specific display mode (manual override).

        This is the preferred method for UI controls, allowing explicit selection
        of slideshow, clock, or black screen mode.

        Override expires at the next scheduled event start time.

        Args:
            mode: One of "slideshow", "clock", or "black"
        """
        if mode not in ("slideshow", "clock", "black"):
            raise ValueError(f"Invalid mode: {mode}. Must be 'slideshow', 'clock', or 'black'")

        if mode == "slideshow":
            self._override = ScheduleState.FORCE_ON
            self._override_mode = None
        else:
            self._override = ScheduleState.FORCE_OFF
            self._override_mode = mode

        self._override_expires = self._get_next_event_start(datetime.now())
        if self._override_expires:
            logger.info(f"Schedule override: {mode} (expires at {self._override_expires})")
        else:
            logger.info(f"Schedule override: {mode} (no expiry)")

    def clear_override(self) -> None:
        """Clear manual override and resume normal schedule immediately."""
        self._override = None
        self._override_expires = None
        self._override_mode = None
        logger.info("Schedule override cleared, resuming normal schedule")

    def has_override(self) -> bool:
        """Check if there's an active manual override."""
        # Check expiry first
        self._check_override_expiry(datetime.now())
        return self._override is not None

    def get_override_expiry(self) -> Optional[datetime]:
        """Get when the current override expires."""
        if self._override is not None:
            return self._override_expires
        return None

    def get_next_transition(self, now: Optional[datetime] = None) -> Optional[Tuple[datetime, str]]:
        """
        Get the next scheduled transition time where the mode actually changes.

        Skips transitions where the mode stays the same (e.g., black → black at midnight).

        Args:
            now: Current datetime.

        Returns:
            Tuple of (datetime, description) or None if scheduling disabled.
        """
        if not self.config.schedule.enabled:
            return None

        if now is None:
            now = datetime.now()

        # Check and clear expired override
        self._check_override_expiry(now)

        # If there's a manual override, return the expiry time
        if self._override is not None and self._override_expires is not None:
            mode = self.get_display_mode(now)
            return (self._override_expires, f"override expires (resume schedule)")

        # Find current event
        current_event = self._get_current_event(now)
        if not current_event:
            return None

        current_mode = current_event.mode

        # Find current event index in today's events
        events_today = self._get_events_for_day(now)
        current_idx = None
        for i, event in enumerate(events_today):
            if event.start_time == current_event.start_time:
                current_idx = i
                break

        if current_idx is None:
            return None

        # Look at remaining events today
        for i in range(current_idx + 1, len(events_today)):
            event = events_today[i]
            if event.mode != current_mode:
                start = self._parse_time(event.start_time)
                next_dt = now.replace(
                    hour=start.hour,
                    minute=start.minute,
                    second=0,
                    microsecond=0
                )
                return (next_dt, f"switch to {event.mode}")

        # Look up to 7 days ahead for next mode change
        # (handles cases like weekdays all black, weekends have slideshow)
        for days_ahead in range(1, 8):
            future_day = now + timedelta(days=days_ahead)
            future_events = self._get_events_for_day(future_day)
            for event in future_events:
                if event.mode != current_mode:
                    start = self._parse_time(event.start_time)
                    next_dt = future_day.replace(
                        hour=start.hour,
                        minute=start.minute,
                        second=0,
                        microsecond=0
                    )
                    return (next_dt, f"switch to {event.mode}")

        # No mode change found (all events have same mode) - return end of current event
        end = self._parse_time(current_event.end_time)
        if current_event.end_time == "24:00":
            return None  # No actual transition
        next_dt = now.replace(
            hour=end.hour,
            minute=end.minute,
            second=0,
            microsecond=0
        )
        return (next_dt, "end of day schedule")

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
        is_weekend = day >= 5
        is_holiday = self._is_today_holiday(now)

        # Get events for today
        events = self._get_events_for_day(now)
        events_data = [
            {
                "start_time": e.start_time,
                "end_time": e.end_time,
                "mode": e.mode
            }
            for e in events
        ]

        # Legacy schedule (for backward compatibility)
        legacy_schedule = self._get_schedule_for_day(day)
        is_override = legacy_schedule == self.config.schedule.overrides.get(day_name)

        return {
            "day": day_name.capitalize(),
            "is_weekend": is_weekend,
            "is_holiday": is_holiday,
            "using_weekend_schedule": is_weekend or is_holiday,
            "is_override": is_override,
            "start_time": legacy_schedule.start_time,
            "end_time": legacy_schedule.end_time,
            "events": events_data,
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

        # Check and clear expired override
        self._check_override_expiry(now)

        state = self.get_current_state(now)
        display_mode = self.get_display_mode(now)
        next_transition = self.get_next_transition(now)
        today = self.get_today_schedule(now)

        # Determine mode_reason: why is the current mode active?
        # Possible values: "scheduled", "manual", "disabled"
        if self._override is not None:
            mode_reason = "manual"
        elif not self.config.schedule.enabled:
            mode_reason = "disabled"
        else:
            mode_reason = "scheduled"

        # Get override info
        override_info = None
        if self._override is not None:
            override_info = {
                "type": "force_on" if self._override == ScheduleState.FORCE_ON else "force_off",
                "mode": self._override_mode,  # The specific mode for force_off
                "expires": self._override_expires.isoformat() if self._override_expires else None
            }

        # Get holiday info
        holiday_info = {
            "enabled": self.config.schedule.holidays.use_weekend_schedule,
            "countries": self.config.schedule.holidays.countries,
            "is_holiday_today": self._is_today_holiday(now)
        }

        return {
            "state": state.value,
            "display_mode": display_mode,
            "mode_reason": mode_reason,
            "should_show_slideshow": self.should_show_slideshow(now),
            "off_hours_mode": self.get_off_hours_mode(),
            "default_screensaver_mode": self.config.schedule.default_screensaver_mode,
            "has_override": self.has_override(),
            "override": override_info,
            "holidays": holiday_info,
            "next_transition": {
                "time": next_transition[0].isoformat() if next_transition else None,
                "description": next_transition[1] if next_transition else None
            },
            "today": today,
            "current_time": now.isoformat()
        }
