# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""Weather data provider using Open-Meteo API."""

import logging
import threading
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

if TYPE_CHECKING:
    from ...config import WeatherConfig

logger = logging.getLogger(__name__)


# WMO Weather interpretation codes
# https://open-meteo.com/en/docs
WMO_CODES = {
    0: "Clear",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Light Drizzle",
    53: "Drizzle",
    55: "Heavy Drizzle",
    56: "Light Freezing Drizzle",
    57: "Freezing Drizzle",
    61: "Light Rain",
    63: "Rain",
    65: "Heavy Rain",
    66: "Light Freezing Rain",
    67: "Freezing Rain",
    71: "Light Snow",
    73: "Snow",
    75: "Heavy Snow",
    77: "Snow Grains",
    80: "Light Showers",
    81: "Showers",
    82: "Heavy Showers",
    85: "Light Snow Showers",
    86: "Snow Showers",
    95: "Thunderstorm",
    96: "Thunderstorm with Hail",
    99: "Thunderstorm with Hail",
}


class WeatherProvider:
    """Fetches weather data from Open-Meteo API with caching."""

    API_URL = "https://api.open-meteo.com/v1/forecast"
    DEFAULT_UPDATE_INTERVAL = 30 * 60  # 30 minutes

    def __init__(self, config: 'WeatherConfig'):
        """Initialize the weather provider.

        Args:
            config: Weather configuration with location and settings.
        """
        self._config = config
        self._cached_data: Optional[dict] = None
        self._cache_time: float = 0
        self._update_interval = config.update_interval_minutes * 60
        self._lock = threading.Lock()

        # Start background update thread if we have location
        if self._has_location():
            self._start_background_updates()

    def _has_location(self) -> bool:
        """Check if location is configured."""
        return (
            self._config.latitude is not None and
            self._config.longitude is not None
        )

    def _start_background_updates(self) -> None:
        """Start background thread for weather updates."""
        thread = threading.Thread(target=self._background_update_loop, daemon=True)
        thread.start()

    def _background_update_loop(self) -> None:
        """Background loop to fetch weather data."""
        while True:
            try:
                self._fetch_weather()
            except Exception as e:
                logger.debug(f"Background weather fetch failed: {e}")
            time.sleep(self._update_interval)

    def _fetch_weather(self) -> None:
        """Fetch weather data from Open-Meteo API."""
        if not REQUESTS_AVAILABLE:
            logger.warning("requests library not available for weather")
            return

        if not self._has_location():
            logger.debug("No location configured for weather")
            return

        try:
            params = {
                'latitude': self._config.latitude,
                'longitude': self._config.longitude,
                'current': 'temperature_2m,weather_code',
                'temperature_unit': 'fahrenheit' if self._config.units == 'fahrenheit' else 'celsius',
                'timezone': 'auto',
            }

            response = requests.get(self.API_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            with self._lock:
                self._cached_data = data
                self._cache_time = time.time()

            current = data.get('current', {})
            temp = current.get('temperature_2m')
            code = current.get('weather_code')
            logger.info(f"Weather fetched: {temp}°, code={code}")

        except requests.RequestException as e:
            logger.warning(f"Failed to fetch weather: {e}")

    def _is_cache_valid(self) -> bool:
        """Check if cached data is still valid."""
        if self._cached_data is None:
            return False
        age = time.time() - self._cache_time
        return age < self._update_interval

    def get_weather_text(self) -> Optional[str]:
        """Get formatted weather text for display.

        Returns:
            String like "72°F Partly Cloudy" or None if no data.
        """
        # Fetch if cache is empty/expired and we have location
        if not self._is_cache_valid() and self._has_location():
            self._fetch_weather()

        with self._lock:
            if not self._cached_data:
                return None

            current = self._cached_data.get('current', {})
            temp = current.get('temperature_2m')
            weather_code = current.get('weather_code')

            if temp is None:
                return None

            # Format temperature
            unit = "°F" if self._config.units == 'fahrenheit' else "°C"
            temp_str = f"{int(round(temp))}{unit}"

            # Get weather description
            condition = WMO_CODES.get(weather_code, "")

            # Include city name if configured
            if self._config.city_name:
                if condition:
                    return f"{self._config.city_name}: {temp_str} {condition}"
                return f"{self._config.city_name}: {temp_str}"

            if condition:
                return f"{temp_str} {condition}"
            return temp_str

    def get_temperature(self) -> Optional[float]:
        """Get current temperature."""
        with self._lock:
            if self._cached_data:
                return self._cached_data.get('current', {}).get('temperature_2m')
        return None

    def get_condition(self) -> Optional[str]:
        """Get current weather condition."""
        with self._lock:
            if self._cached_data:
                code = self._cached_data.get('current', {}).get('weather_code')
                return WMO_CODES.get(code)
        return None
