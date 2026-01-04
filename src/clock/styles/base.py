# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""Base class for clock style implementations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple, List
import pygame


@dataclass
class ClockRenderContext:
    """Context passed to clock style render methods."""
    screen_width: int
    screen_height: int
    now: datetime
    size: str  # 'small', 'medium', 'large'
    show_date: bool
    weather_text: Optional[str] = None
    news_headline: Optional[str] = None
    # Position offset for burn-in prevention
    offset_x: int = 0
    offset_y: int = 0


@dataclass
class RenderedElement:
    """A rendered surface with position information."""
    surface: pygame.Surface
    x: int
    y: int

    @property
    def width(self) -> int:
        return self.surface.get_width()

    @property
    def height(self) -> int:
        return self.surface.get_height()


class BaseClockStyle(ABC):
    """Abstract base class for clock styles.

    Each clock style is responsible for rendering the time (and optionally date)
    to pygame surfaces. The ClockRenderer handles converting these to SDL textures
    and compositing with weather/news overlays.
    """

    # Style metadata - subclasses should override
    name: str = "base"
    display_name: str = "Base Clock"
    supports_seconds: bool = False

    # Size presets (font sizes as percentage of screen height)
    # Subclasses can override for different proportions
    SIZE_PRESETS = {
        'small': {'time_scale': 0.08, 'date_scale': 0.03},
        'medium': {'time_scale': 0.12, 'date_scale': 0.04},
        'large': {'time_scale': 0.18, 'date_scale': 0.05},
    }

    # Font preferences for digital clocks (tried in order)
    DIGITAL_TIME_FONTS = ['Cantarell', 'DejaVu Sans Light', 'Nimbus Sans', 'Liberation Sans']
    DIGITAL_DATE_FONTS = ['Cantarell', 'DejaVu Sans', 'Liberation Sans']

    def __init__(self):
        """Initialize the clock style."""
        self._font_cache: dict = {}

    def get_font(self, size: int, bold: bool = False, italic: bool = False,
                 font_name: str = None) -> pygame.font.Font:
        """Get a cached pygame font.

        Args:
            size: Font size in pixels.
            bold: Whether to use bold font.
            italic: Whether to use italic font.
            font_name: Specific font family name (None for system default).

        Returns:
            pygame.font.Font instance.
        """
        cache_key = (size, bold, italic, font_name)
        if cache_key not in self._font_cache:
            self._font_cache[cache_key] = pygame.font.SysFont(
                font_name, size, bold=bold, italic=italic
            )
        return self._font_cache[cache_key]

    def get_digital_font(self, size: int, for_time: bool = True) -> pygame.font.Font:
        """Get a clean, thin font suitable for digital clock display.

        Args:
            size: Font size in pixels.
            for_time: True for time display (thinner), False for date.

        Returns:
            pygame.font.Font instance.
        """
        fonts = self.DIGITAL_TIME_FONTS if for_time else self.DIGITAL_DATE_FONTS
        cache_key = ('digital', size, for_time)

        if cache_key not in self._font_cache:
            # Try preferred fonts in order
            font = None
            for font_name in fonts:
                try:
                    font = pygame.font.SysFont(font_name, size, bold=False)
                    # Verify font was actually found (pygame falls back to default)
                    if font.get_linesize() > 0:
                        break
                except Exception:
                    continue
            if font is None:
                font = pygame.font.SysFont(None, size, bold=False)
            self._font_cache[cache_key] = font

        return self._font_cache[cache_key]

    def get_scaled_size(self, ctx: ClockRenderContext, scale_key: str) -> int:
        """Get a font size scaled to screen height.

        Args:
            ctx: Render context with screen dimensions.
            scale_key: Either 'time_scale' or 'date_scale'.

        Returns:
            Font size in pixels.
        """
        preset = self.SIZE_PRESETS.get(ctx.size, self.SIZE_PRESETS['medium'])
        scale = preset.get(scale_key, 0.1)
        return int(ctx.screen_height * scale)

    @abstractmethod
    def render(self, ctx: ClockRenderContext) -> List[RenderedElement]:
        """Render the clock display.

        Args:
            ctx: Context containing screen size, current time, and options.

        Returns:
            List of RenderedElement objects to be drawn.
        """
        pass

    def get_update_interval_ms(self) -> int:
        """Return the recommended update interval in milliseconds.

        Styles showing seconds should return 100-200ms for smooth updates.
        Styles showing only minutes can return 1000ms.
        """
        return 100 if self.supports_seconds else 1000

    def _center_x(self, surface: pygame.Surface, ctx: ClockRenderContext) -> int:
        """Calculate centered X position for a surface."""
        return (ctx.screen_width - surface.get_width()) // 2 + ctx.offset_x

    def _render_text(
        self,
        text: str,
        font: pygame.font.Font,
        color: Tuple[int, int, int] = (255, 255, 255),
        antialias: bool = True
    ) -> pygame.Surface:
        """Render text to a surface.

        Args:
            text: Text to render.
            font: Font to use.
            color: RGB color tuple.
            antialias: Whether to antialias the text.

        Returns:
            pygame.Surface with rendered text.
        """
        return font.render(text, antialias, color)
