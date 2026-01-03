# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""Clock renderer orchestrator."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING, Any

import pygame

from .styles import CLOCK_STYLES, BaseClockStyle
from .styles.base import ClockRenderContext, RenderedElement

if TYPE_CHECKING:
    from ..config import ClockConfig, WeatherConfig, NewsConfig

logger = logging.getLogger(__name__)


class ClockRenderer:
    """Orchestrates clock rendering with weather and news overlays.

    This class manages:
    - Clock style selection and rendering
    - Weather and news data provider integration
    - Burn-in prevention through position drift
    - Conversion of pygame surfaces to SDL2 textures
    """

    # Burn-in prevention settings
    DRIFT_RANGE_X = 50  # Max pixels to drift horizontally
    DRIFT_RANGE_Y = 30  # Max pixels to drift vertically
    DRIFT_PERIOD_S = 300  # Full drift cycle in seconds (5 minutes)

    def __init__(
        self,
        renderer: Any,
        screen_width: int,
        screen_height: int,
        clock_config: ClockConfig,
        weather_config: Optional[WeatherConfig] = None,
        news_config: Optional[NewsConfig] = None,
        surface_to_texture_fn=None
    ):
        """Initialize the clock renderer.

        Args:
            renderer: SDL2 renderer for drawing.
            screen_width: Screen width in pixels.
            screen_height: Screen height in pixels.
            clock_config: Clock configuration.
            weather_config: Optional weather configuration.
            news_config: Optional news configuration.
            surface_to_texture_fn: Function to convert pygame surfaces to SDL textures.
        """
        self._renderer = renderer
        self._screen_width = screen_width
        self._screen_height = screen_height
        self._clock_config = clock_config
        self._weather_config = weather_config
        self._news_config = news_config
        self._surface_to_texture = surface_to_texture_fn

        # Initialize clock style
        self._style: Optional[BaseClockStyle] = None
        self._load_style(clock_config.style)

        # Data providers (lazy loaded)
        self._weather_provider = None
        self._news_provider = None

        # Cached weather/news data
        self._weather_text: Optional[str] = None
        self._news_headline: Optional[str] = None

        # News ticker state
        self._ticker_headlines: list = []  # List of headline strings
        self._ticker_widths: list = []  # Cached width of each headline
        self._ticker_surfaces: list = []  # Cached rendered surfaces
        self._ticker_separator = "   ·   "  # Separator between headlines (middle dot)
        self._ticker_sep_width: int = 0  # Width of separator
        self._ticker_sep_surface = None  # Cached separator surface
        self._ticker_total_width: int = 0  # Total virtual width of all headlines + separators
        self._ticker_offset: float = 0.0  # Scroll offset into virtual ticker
        # Use config scroll speed if available, default 180 px/s
        self._ticker_speed: float = float(news_config.scroll_speed) if news_config else 180.0
        self._ticker_font_size: int = 0  # Cached font size
        self._last_ticker_update: float = time.time()

        # Drift state
        self._drift_start_time = time.time()

        logger.info(f"ClockRenderer initialized with style: {clock_config.style}")

    def _load_style(self, style_name: str) -> None:
        """Load a clock style by name."""
        style_class = CLOCK_STYLES.get(style_name)
        if style_class:
            self._style = style_class()
            logger.debug(f"Loaded clock style: {style_name}")
        else:
            logger.warning(f"Unknown clock style '{style_name}', using digital_24h")
            self._style = CLOCK_STYLES['digital_24h']()

    def set_style(self, style_name: str) -> None:
        """Change the clock style."""
        if style_name != self._clock_config.style:
            self._load_style(style_name)
            self._clock_config.style = style_name
            logger.info(f"Clock style changed to: {style_name}")

    def set_size(self, size: str) -> None:
        """Change the clock size."""
        if size in ('small', 'medium', 'large'):
            self._clock_config.size = size
            logger.info(f"Clock size changed to: {size}")

    def update_config(
        self,
        clock_config: ClockConfig,
        weather_config: Optional[WeatherConfig] = None,
        news_config: Optional[NewsConfig] = None
    ) -> None:
        """Update all clock configuration.

        Called when settings are changed via the web UI.
        """
        # Update clock settings
        if clock_config.style != self._clock_config.style:
            self._load_style(clock_config.style)
        self._clock_config = clock_config

        # Update weather config - reinitialize provider if settings changed
        if weather_config:
            old_enabled = self._weather_config.enabled if self._weather_config else False
            self._weather_config = weather_config
            logger.info(f"Weather config updated: enabled={weather_config.enabled}, font_size={weather_config.font_size}")
            if weather_config.enabled and not old_enabled:
                self._weather_provider = None  # Force re-init
                self._weather_text = None
            elif not weather_config.enabled:
                # Clear cached data when disabled
                self._weather_text = None

        # Update news config - reinitialize provider if settings changed
        if news_config:
            old_enabled = self._news_config.enabled if self._news_config else False
            old_font_size = self._news_config.font_size if self._news_config else 0
            self._news_config = news_config
            logger.info(f"News config updated: enabled={news_config.enabled}, font_size={news_config.font_size}, old_font_size={old_font_size}")
            # Update scroll speed from config
            self._ticker_speed = float(news_config.scroll_speed)
            # Invalidate cached surfaces if font size changed
            if news_config.font_size != old_font_size:
                logger.info(f"News font_size changed, invalidating cache")
                self._ticker_surfaces = []
                self._ticker_widths = []
                self._ticker_font_size = 0  # Force recalculation
            if news_config.enabled and not old_enabled:
                self._news_provider = None  # Force re-init
                self._news_headline = None
                self._ticker_headlines = []
            elif not news_config.enabled:
                # Clear cached data when disabled
                self._news_headline = None
                self._ticker_headlines = []
                self._ticker_surfaces = []

        logger.info(f"Clock config updated: style={clock_config.style}, size={clock_config.size}")

    def update_dimensions(self, width: int, height: int) -> None:
        """Update screen dimensions after resolution change."""
        self._screen_width = width
        self._screen_height = height

    def update_renderer(self, renderer: Any) -> None:
        """Update the SDL renderer reference."""
        self._renderer = renderer

    def _get_drift_offset(self) -> tuple[int, int]:
        """Calculate current burn-in prevention offset.

        Uses a smooth sinusoidal drift pattern to prevent OLED/plasma burn-in.
        """
        elapsed = time.time() - self._drift_start_time
        # Use different periods for X and Y to create a Lissajous-like pattern
        x_phase = (elapsed / self.DRIFT_PERIOD_S) * 2 * math.pi
        y_phase = (elapsed / (self.DRIFT_PERIOD_S * 0.7)) * 2 * math.pi

        offset_x = int(math.sin(x_phase) * self.DRIFT_RANGE_X)
        offset_y = int(math.sin(y_phase) * self.DRIFT_RANGE_Y)

        return offset_x, offset_y

    def _init_weather_provider(self) -> None:
        """Lazily initialize the weather provider."""
        if self._weather_provider is None and self._weather_config and self._weather_config.enabled:
            try:
                from .providers.weather import WeatherProvider
                self._weather_provider = WeatherProvider(self._weather_config)
                logger.info("Weather provider initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize weather provider: {e}")

    def _init_news_provider(self) -> None:
        """Lazily initialize the news provider."""
        if self._news_provider is None and self._news_config and self._news_config.enabled:
            try:
                from .providers.news import NewsProvider
                self._news_provider = NewsProvider(self._news_config)
                logger.info("News provider initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize news provider: {e}")

    def _update_data(self) -> None:
        """Update weather and news data from providers."""
        # Update weather
        if self._weather_provider:
            try:
                self._weather_text = self._weather_provider.get_weather_text()
            except Exception as e:
                logger.debug(f"Weather update failed: {e}")

        # Update news ticker headlines
        if self._news_provider:
            try:
                headlines = self._news_provider.get_all_headlines()
                if headlines and headlines != self._ticker_headlines:
                    self._ticker_headlines = headlines
                    # Reset cached widths, surfaces, and offset - will be recalculated on render
                    self._ticker_widths = []
                    self._ticker_surfaces = []
                    self._ticker_sep_surface = None
                    self._ticker_total_width = 0
                    self._ticker_offset = 0.0  # Reset scroll position for new content
            except Exception as e:
                logger.debug(f"News update failed: {e}")

    # Size multipliers for weather and news text (relative to screen height)
    # These scale with the clock size setting
    SIZE_SCALES = {
        'small': {'weather': 0.035, 'news': 0.038},
        'medium': {'weather': 0.045, 'news': 0.048},
        'large': {'weather': 0.055, 'news': 0.058},
    }

    # News ticker separator
    TICKER_SEPARATOR = "  •  "

    def render(self) -> None:
        """Render the clock display.

        Clears the screen, renders the clock, weather, and news overlays,
        and presents the frame.
        """
        if not self._style:
            return

        # Initialize providers if needed
        self._init_weather_provider()
        self._init_news_provider()

        # Update data from providers
        self._update_data()

        # Clear screen to black
        self._renderer.draw_color = (0, 0, 0, 255)
        self._renderer.clear()

        # Calculate drift offset
        offset_x, offset_y = self._get_drift_offset()

        # Create render context
        ctx = ClockRenderContext(
            screen_width=self._screen_width,
            screen_height=self._screen_height,
            now=datetime.now(),
            size=self._clock_config.size,
            show_date=self._clock_config.show_date,
            weather_text=self._weather_text,
            news_headline=self._news_headline,
            offset_x=offset_x,
            offset_y=offset_y,
        )

        # Get rendered elements from style
        elements = self._style.render(ctx)

        # Track the bottom of clock elements for weather positioning
        clock_bottom = 0

        # Convert pygame surfaces to SDL textures and draw
        for element in elements:
            if self._surface_to_texture:
                texture = self._surface_to_texture(element.surface)
                texture.draw(dstrect=(
                    element.x,
                    element.y,
                    element.width,
                    element.height
                ))
                # Track lowest point of clock content
                element_bottom = element.y + element.height
                if element_bottom > clock_bottom:
                    clock_bottom = element_bottom

        # Render weather below clock (centered, integrated)
        if self._weather_text:
            self._render_weather(ctx, clock_bottom)

        # Render scrolling news ticker at bottom of screen
        if self._ticker_headlines:
            self._render_news_ticker(ctx)

        # Note: Caller is responsible for calling renderer.present()

    def _render_weather(self, ctx: ClockRenderContext, clock_bottom: int) -> None:
        """Render weather information below the clock, centered.

        Args:
            ctx: Render context with screen dimensions and settings.
            clock_bottom: Y coordinate of the bottom of the clock content.
        """
        # Use explicit font size if configured, otherwise auto-scale
        if self._weather_config and self._weather_config.font_size > 0:
            font_size = self._weather_config.font_size
        else:
            scales = self.SIZE_SCALES.get(ctx.size, self.SIZE_SCALES['medium'])
            font_size = int(ctx.screen_height * scales['weather'])
        font = pygame.font.SysFont(None, font_size, italic=True)

        # Render weather text in a subtle gray
        weather_surface = font.render(self._weather_text, True, (160, 160, 160))

        # Position centered, below the clock with generous spacing
        spacing = int(ctx.screen_height * 0.05)  # 5% of screen height
        x = (ctx.screen_width - weather_surface.get_width()) // 2 + ctx.offset_x
        y = clock_bottom + spacing + ctx.offset_y

        if self._surface_to_texture:
            texture = self._surface_to_texture(weather_surface)
            texture.draw(dstrect=(x, y, weather_surface.get_width(), weather_surface.get_height()))

    def _render_news_ticker(self, ctx: ClockRenderContext) -> None:
        """Render scrolling news ticker at bottom of screen.

        Shows multiple headlines separated by middle dots, scrolling continuously.
        Only renders headlines that are currently visible on screen.
        """
        if not self._ticker_headlines:
            return

        # Safety: ensure widths match headlines count (guard against timing issues)
        if self._ticker_widths and len(self._ticker_widths) != len(self._ticker_headlines):
            self._ticker_widths = []  # Force recalculation
            self._ticker_surfaces = []
            self._ticker_sep_surface = None
            self._ticker_total_width = 0

        # Use explicit font size if configured, otherwise auto-scale
        if self._news_config and self._news_config.font_size > 0:
            font_size = self._news_config.font_size
        else:
            scales = self.SIZE_SCALES.get(ctx.size, self.SIZE_SCALES['medium'])
            font_size = int(ctx.screen_height * scales['news'])

        # Check if we need to recalculate widths and cache surfaces
        if font_size != self._ticker_font_size or not self._ticker_widths:
            logger.info(f"Rebuilding ticker surfaces: font_size={font_size}, old={self._ticker_font_size}, headlines={len(self._ticker_headlines)}")
            self._ticker_font_size = font_size
            font = pygame.font.SysFont(None, font_size)

            # Pre-render and cache all headline surfaces
            # Truncate headlines that would exceed SDL2's 4096px texture limit
            max_width = 4000  # Leave some margin below 4096
            self._ticker_widths = []
            self._ticker_surfaces = []
            for headline in self._ticker_headlines:
                # Try rendering, truncate if too wide
                truncated = headline
                surface = font.render(truncated, True, (160, 160, 160))
                original_width = surface.get_width()
                while surface.get_width() > max_width and len(truncated) > 10:
                    truncated = truncated[:-4] + "..."
                    surface = font.render(truncated, True, (160, 160, 160))
                if truncated != headline:
                    logger.debug(f"Headline truncated: {original_width}px -> {surface.get_width()}px")
                self._ticker_widths.append(surface.get_width())
                self._ticker_surfaces.append(surface)

            # Pre-render separator
            self._ticker_sep_surface = font.render(self._ticker_separator, True, (100, 100, 100))
            self._ticker_sep_width = self._ticker_sep_surface.get_width()

            # Calculate total virtual width (all headlines + separators)
            self._ticker_total_width = sum(self._ticker_widths) + self._ticker_sep_width * len(self._ticker_headlines)

        # Calculate time delta and update scroll offset
        now = time.time()
        delta = now - self._last_ticker_update
        self._last_ticker_update = now

        # Clamp delta to avoid jumps after pause/lag (max 100ms worth of movement)
        delta = min(delta, 0.1)

        # Move ticker left (offset grows continuously)
        self._ticker_offset += self._ticker_speed * delta

        # Position at bottom with padding
        padding = int(ctx.screen_height * 0.025)
        y = ctx.screen_height - font_size - padding + ctx.offset_y

        # The ticker is a repeating pattern of width = total_width
        # We need to figure out which "copy" of the pattern to start rendering from
        # based on the current offset, and render enough copies to fill the screen

        if self._ticker_total_width <= 0:
            return

        # Calculate the first copy that could possibly have visible content
        # A copy is visible if its right edge (copy_start_x + total_width) > 0
        # Solve: screen_width - offset + i * total_width + total_width > 0
        # i > (offset - screen_width - total_width) / total_width
        first_copy = max(0, int((self._ticker_offset - ctx.screen_width) / self._ticker_total_width))

        # Render enough copies to definitely cover the screen plus buffer
        num_copies = int(ctx.screen_width / self._ticker_total_width) + 3

        # Render ticker copies
        num_headlines = len(self._ticker_headlines)
        for copy_idx in range(first_copy, first_copy + num_copies):
            copy_start_x = ctx.screen_width - self._ticker_offset + copy_idx * self._ticker_total_width

            # Skip this copy if completely off-screen right (not yet visible)
            if copy_start_x > ctx.screen_width:
                continue
            # Skip if completely off-screen left (already scrolled past)
            if copy_start_x + self._ticker_total_width < 0:
                continue

            virtual_pos = 0
            for i in range(num_headlines):
                headline_width = self._ticker_widths[i]

                # Calculate screen X position for this headline
                screen_x = copy_start_x + virtual_pos + ctx.offset_x

                # Render if any part is visible (right edge > 0 AND left edge < screen width)
                if screen_x + headline_width > 0 and screen_x < ctx.screen_width:
                    if self._surface_to_texture and i < len(self._ticker_surfaces):
                        texture = self._surface_to_texture(self._ticker_surfaces[i])
                        texture.draw(dstrect=(int(screen_x), y, headline_width, self._ticker_surfaces[i].get_height()))

                virtual_pos += headline_width

                # Render separator after headline
                sep_screen_x = copy_start_x + virtual_pos + ctx.offset_x
                if sep_screen_x + self._ticker_sep_width > 0 and sep_screen_x < ctx.screen_width:
                    if self._surface_to_texture and self._ticker_sep_surface:
                        texture = self._surface_to_texture(self._ticker_sep_surface)
                        texture.draw(dstrect=(int(sep_screen_x), y, self._ticker_sep_width, self._ticker_sep_surface.get_height()))

                virtual_pos += self._ticker_sep_width

        # Prevent offset from growing too large (wrap at a safe boundary)
        # This maintains visual continuity while preventing float overflow
        if self._ticker_offset > self._ticker_total_width * 1000:
            self._ticker_offset = self._ticker_offset % self._ticker_total_width

    def get_update_interval_ms(self) -> int:
        """Get the recommended update interval for current style.

        Returns faster interval when news ticker is active for smooth scrolling.
        """
        # News ticker needs fast updates for smooth animation (~60fps)
        if self._ticker_headlines:
            return 16  # ~60fps for smooth ticker

        # Otherwise use style's interval (100ms for seconds, 1000ms otherwise)
        if self._style:
            return self._style.get_update_interval_ms()
        return 1000

    @staticmethod
    def get_available_styles() -> list[dict]:
        """Get list of available clock styles with metadata."""
        return [
            {
                'id': style_id,
                'name': style_class.display_name,
                'supports_seconds': style_class.supports_seconds,
            }
            for style_id, style_class in CLOCK_STYLES.items()
        ]
