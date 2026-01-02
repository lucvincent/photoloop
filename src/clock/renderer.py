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
            if weather_config.enabled and not old_enabled:
                self._weather_provider = None  # Force re-init
                self._weather_text = None

        # Update news config - reinitialize provider if settings changed
        if news_config:
            old_enabled = self._news_config.enabled if self._news_config else False
            self._news_config = news_config
            if news_config.enabled and not old_enabled:
                self._news_provider = None  # Force re-init
                self._news_headline = None

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

        # Update news
        if self._news_provider:
            try:
                self._news_headline = self._news_provider.get_current_headline()
            except Exception as e:
                logger.debug(f"News update failed: {e}")

    # Size multipliers for weather and news text (relative to screen height)
    # These scale with the clock size setting
    SIZE_SCALES = {
        'small': {'weather': 0.035, 'news': 0.028},
        'medium': {'weather': 0.045, 'news': 0.035},
        'large': {'weather': 0.055, 'news': 0.042},
    }

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

        # Render news ticker at bottom of screen
        if self._news_headline:
            self._render_news(ctx)

        # Note: Caller is responsible for calling renderer.present()

    def _render_weather(self, ctx: ClockRenderContext, clock_bottom: int) -> None:
        """Render weather information below the clock, centered.

        Args:
            ctx: Render context with screen dimensions and settings.
            clock_bottom: Y coordinate of the bottom of the clock content.
        """
        # Get size-scaled font (italic for visual differentiation)
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

    def _render_news(self, ctx: ClockRenderContext) -> None:
        """Render news headline at bottom of screen, centered."""
        # Get size-scaled font
        scales = self.SIZE_SCALES.get(ctx.size, self.SIZE_SCALES['medium'])
        font_size = int(ctx.screen_height * scales['news'])
        font = pygame.font.SysFont(None, font_size)

        # Render news in a subtle gray
        news_surface = font.render(self._news_headline, True, (140, 140, 140))

        # Position centered at bottom with padding
        padding = int(ctx.screen_height * 0.03)  # 3% from bottom
        x = (ctx.screen_width - news_surface.get_width()) // 2 + ctx.offset_x
        y = ctx.screen_height - news_surface.get_height() - padding + ctx.offset_y

        if self._surface_to_texture:
            texture = self._surface_to_texture(news_surface)
            texture.draw(dstrect=(x, y, news_surface.get_width(), news_surface.get_height()))

    def get_update_interval_ms(self) -> int:
        """Get the recommended update interval for current style."""
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
