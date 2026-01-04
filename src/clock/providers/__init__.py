# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""Data providers for clock display (weather, news)."""

from .weather import WeatherProvider
from .news import NewsProvider

__all__ = ['WeatherProvider', 'NewsProvider']
