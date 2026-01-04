# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""Clock style implementations."""

from .base import BaseClockStyle
from .digital import Digital24HStyle, Digital12HStyle, DigitalSecondsStyle, DigitalLargeStyle
from .minimal import MinimalTimeStyle, MinimalDateStyle
from .analog import AnalogClassicStyle, AnalogModernStyle

# Registry of available clock styles
CLOCK_STYLES = {
    'digital_24h': Digital24HStyle,
    'digital_12h': Digital12HStyle,
    'digital_seconds': DigitalSecondsStyle,
    'digital_large': DigitalLargeStyle,
    'analog_classic': AnalogClassicStyle,
    'analog_modern': AnalogModernStyle,
    'minimal_time': MinimalTimeStyle,
    'minimal_date': MinimalDateStyle,
}

__all__ = [
    'BaseClockStyle',
    'Digital24HStyle',
    'Digital12HStyle',
    'DigitalSecondsStyle',
    'DigitalLargeStyle',
    'AnalogClassicStyle',
    'AnalogModernStyle',
    'MinimalTimeStyle',
    'MinimalDateStyle',
    'CLOCK_STYLES',
]
