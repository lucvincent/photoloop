"""
Display engine for PhotoLoop.
Handles pygame rendering, transitions, overlays, and Ken Burns effects.
"""

import logging
import os
import time
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

import pygame
from PIL import Image

from .cache_manager import CachedMedia
from .config import PhotoLoopConfig, OverlayConfig
from .image_processor import DisplayParams, ImageProcessor
from .metadata import format_date

logger = logging.getLogger(__name__)


class TransitionType(Enum):
    """Types of transitions between photos."""
    NONE = "none"
    FADE = "fade"
    SLIDE_LEFT = "slide_left"
    SLIDE_RIGHT = "slide_right"
    SLIDE_UP = "slide_up"
    SLIDE_DOWN = "slide_down"


class DisplayMode(Enum):
    """Current display mode."""
    SLIDESHOW = "slideshow"
    BLACK = "black"
    CLOCK = "clock"


class Display:
    """
    Main display engine using pygame.

    Handles:
    - Full-screen display
    - Photo rendering with Ken Burns effect
    - Transitions between photos
    - Metadata overlay
    - Clock and black screen modes
    """

    def __init__(self, config: PhotoLoopConfig):
        """
        Initialize the display.

        Args:
            config: PhotoLoop configuration.
        """
        self.config = config

        # Initialize pygame
        pygame.init()
        pygame.mouse.set_visible(False)

        # Get screen dimensions
        if config.display.resolution == "auto":
            info = pygame.display.Info()
            self.screen_width = info.current_w
            self.screen_height = info.current_h
        else:
            parts = config.display.resolution.lower().split('x')
            self.screen_width = int(parts[0])
            self.screen_height = int(parts[1])

        logger.info(f"Display resolution: {self.screen_width}x{self.screen_height}")

        # Create display - check for windowed mode (useful for VNC)
        windowed = os.environ.get("PHOTOLOOP_WINDOWED", "").lower() in ("1", "true", "yes")
        if windowed:
            logger.info("Running in windowed mode (PHOTOLOOP_WINDOWED set)")
            self.screen = pygame.display.set_mode(
                (self.screen_width, self.screen_height),
                pygame.HWSURFACE | pygame.DOUBLEBUF
            )
        else:
            self.screen = pygame.display.set_mode(
                (self.screen_width, self.screen_height),
                pygame.FULLSCREEN | pygame.HWSURFACE | pygame.DOUBLEBUF
            )
        pygame.display.set_caption("PhotoLoop")

        # Current state
        self.mode = DisplayMode.BLACK
        self._current_surface: Optional[pygame.Surface] = None
        self._next_surface: Optional[pygame.Surface] = None
        self._current_media: Optional[CachedMedia] = None
        self._current_params: Optional[DisplayParams] = None

        # Transition state
        self._transitioning = False
        self._transition_start = 0
        self._transition_type = TransitionType.FADE

        # Ken Burns state
        self._kb_start_time = 0
        self._kb_duration = config.display.photo_duration_seconds
        self._source_image: Optional[Image.Image] = None

        # Image processor
        self._processor = ImageProcessor(
            screen_width=self.screen_width,
            screen_height=self.screen_height,
            scaling_mode=config.scaling.mode,
            face_position=config.scaling.face_position,
            fallback_crop=config.scaling.fallback_crop,
            ken_burns_enabled=config.ken_burns.enabled,
            ken_burns_zoom_range=tuple(config.ken_burns.zoom_range),
            ken_burns_pan_speed=config.ken_burns.pan_speed,
            ken_burns_randomize=config.ken_burns.randomize
        )

        # Fonts for overlay and clock
        self._init_fonts()

        # Clock
        self.clock = pygame.time.Clock()
        self.target_fps = 30

    def _init_fonts(self) -> None:
        """Initialize fonts for overlay and clock."""
        # Try to find a good font
        font_names = [
            "DejaVuSans",
            "FreeSans",
            "LiberationSans",
            "Arial",
            None  # Fallback to default
        ]

        self._overlay_font = None
        self._clock_font = None

        for font_name in font_names:
            try:
                self._overlay_font = pygame.font.SysFont(
                    font_name,
                    self.config.overlay.font_size
                )
                self._clock_font = pygame.font.SysFont(font_name, 120)
                break
            except Exception:
                continue

        if self._overlay_font is None:
            self._overlay_font = pygame.font.Font(None, self.config.overlay.font_size)
        if self._clock_font is None:
            self._clock_font = pygame.font.Font(None, 120)

    def show_photo(
        self,
        media: CachedMedia,
        params: DisplayParams,
        transition: bool = True
    ) -> None:
        """
        Display a photo with optional transition.

        Args:
            media: Cached media to display.
            params: Display parameters.
            transition: Whether to use transition effect.
        """
        self.mode = DisplayMode.SLIDESHOW
        self._current_media = media
        self._current_params = params

        # Load source image for Ken Burns
        try:
            self._source_image = Image.open(media.local_path)
            if self._source_image.mode != "RGB":
                self._source_image = self._source_image.convert("RGB")
        except Exception as e:
            logger.error(f"Failed to load image {media.local_path}: {e}")
            return

        # Get initial frame
        if self.config.ken_burns.enabled and params.ken_burns:
            frame = self._processor.get_ken_burns_frame(
                self._source_image,
                params.crop_region,
                params.ken_burns,
                0.0  # Start at beginning
            )
        else:
            frame = self._processor.prepare_image_for_display(
                media.local_path,
                params
            )

        # Convert PIL to pygame
        next_surface = self._pil_to_pygame(frame)

        if transition and self._current_surface is not None:
            self._start_transition(next_surface)
        else:
            self._current_surface = next_surface
            self._next_surface = None

        self._kb_start_time = time.time()
        self._kb_duration = self.config.display.photo_duration_seconds

    def _pil_to_pygame(self, pil_image: Image.Image) -> pygame.Surface:
        """Convert PIL Image to pygame Surface."""
        mode = pil_image.mode
        size = pil_image.size
        data = pil_image.tobytes()

        return pygame.image.fromstring(data, size, mode)

    def _start_transition(self, next_surface: pygame.Surface) -> None:
        """Start a transition to a new surface."""
        self._next_surface = next_surface
        self._transitioning = True
        self._transition_start = time.time()

        # Choose transition type
        transition_type = self.config.display.transition_type
        if transition_type == "random":
            import random
            transition_type = random.choice([
                "fade", "slide_left", "slide_right", "slide_up", "slide_down"
            ])

        self._transition_type = TransitionType(transition_type)

    def update(self) -> bool:
        """
        Update the display for the current frame.

        Returns:
            True to continue running, False to quit.
        """
        # Handle events first
        events = self.handle_events()
        if "quit" in events:
            return False

        if self.mode == DisplayMode.BLACK:
            self.screen.fill((0, 0, 0))

        elif self.mode == DisplayMode.CLOCK:
            self._render_clock()

        elif self.mode == DisplayMode.SLIDESHOW:
            if self._transitioning:
                self._render_transition()
            else:
                self._render_slideshow()

        pygame.display.flip()
        self.clock.tick(self.target_fps)
        return True

    def _render_slideshow(self) -> None:
        """Render the current slideshow frame."""
        if self._source_image is None or self._current_params is None:
            if self._current_surface:
                self.screen.blit(self._current_surface, (0, 0))
            return

        # Calculate Ken Burns progress
        elapsed = time.time() - self._kb_start_time
        progress = min(1.0, elapsed / self._kb_duration)

        if self.config.ken_burns.enabled and self._current_params.ken_burns:
            # Render Ken Burns frame
            frame = self._processor.get_ken_burns_frame(
                self._source_image,
                self._current_params.crop_region,
                self._current_params.ken_burns,
                progress
            )
            self._current_surface = self._pil_to_pygame(frame)

        if self._current_surface:
            self.screen.blit(self._current_surface, (0, 0))

        # Render overlay
        if self.config.overlay.enabled and self._current_media:
            self._render_overlay()

    def _render_transition(self) -> None:
        """Render transition between photos."""
        if self._current_surface is None or self._next_surface is None:
            self._transitioning = False
            return

        elapsed = (time.time() - self._transition_start) * 1000
        duration = self.config.display.transition_duration_ms
        progress = min(1.0, elapsed / duration)

        if progress >= 1.0:
            # Transition complete
            self._current_surface = self._next_surface
            self._next_surface = None
            self._transitioning = False
            self.screen.blit(self._current_surface, (0, 0))
            return

        if self._transition_type == TransitionType.FADE:
            self._render_fade_transition(progress)
        elif self._transition_type == TransitionType.SLIDE_LEFT:
            self._render_slide_transition(progress, (-1, 0))
        elif self._transition_type == TransitionType.SLIDE_RIGHT:
            self._render_slide_transition(progress, (1, 0))
        elif self._transition_type == TransitionType.SLIDE_UP:
            self._render_slide_transition(progress, (0, -1))
        elif self._transition_type == TransitionType.SLIDE_DOWN:
            self._render_slide_transition(progress, (0, 1))
        else:
            # No transition
            self.screen.blit(self._next_surface, (0, 0))

    def _render_fade_transition(self, progress: float) -> None:
        """Render a fade transition."""
        # Draw current image
        self.screen.blit(self._current_surface, (0, 0))

        # Draw next image with alpha
        alpha = int(255 * progress)
        self._next_surface.set_alpha(alpha)
        self.screen.blit(self._next_surface, (0, 0))
        self._next_surface.set_alpha(255)

    def _render_slide_transition(
        self,
        progress: float,
        direction: Tuple[int, int]
    ) -> None:
        """Render a slide transition."""
        dx, dy = direction

        # Current image slides out
        current_x = int(dx * self.screen_width * progress)
        current_y = int(dy * self.screen_height * progress)

        # Next image slides in
        next_x = int(-dx * self.screen_width * (1 - progress))
        next_y = int(-dy * self.screen_height * (1 - progress))

        self.screen.fill((0, 0, 0))
        self.screen.blit(self._current_surface, (current_x, current_y))
        self.screen.blit(self._next_surface, (next_x, next_y))

    def _render_overlay(self) -> None:
        """Render the metadata overlay."""
        if not self._current_media:
            return

        overlay_cfg = self.config.overlay
        lines = []

        # Add date
        if overlay_cfg.show_date and self._current_media.exif_date:
            try:
                date = datetime.fromisoformat(self._current_media.exif_date)
                date_str = format_date(date, overlay_cfg.date_format)
                if date_str:
                    lines.append(date_str)
            except Exception:
                pass

        # Add caption
        if overlay_cfg.show_caption and self._current_media.caption:
            caption = self._current_media.caption
            if overlay_cfg.max_caption_length > 0:
                caption = caption[:overlay_cfg.max_caption_length]
                if len(self._current_media.caption) > overlay_cfg.max_caption_length:
                    caption += "..."
            lines.append(caption)

        if not lines:
            return

        # Render text
        text_surfaces = []
        for line in lines:
            # Wrap long lines
            wrapped = self._wrap_text(line, overlay_cfg.font_size)
            for wrapped_line in wrapped:
                surf = self._overlay_font.render(
                    wrapped_line,
                    True,
                    tuple(overlay_cfg.font_color)
                )
                text_surfaces.append(surf)

        if not text_surfaces:
            return

        # Calculate total size
        max_width = max(s.get_width() for s in text_surfaces)
        total_height = sum(s.get_height() for s in text_surfaces)
        padding = overlay_cfg.padding

        # Create background
        bg_width = max_width + padding * 2
        bg_height = total_height + padding * 2
        bg_color = tuple(overlay_cfg.background_color)

        bg_surface = pygame.Surface((bg_width, bg_height), pygame.SRCALPHA)
        bg_surface.fill(bg_color)

        # Determine position
        if overlay_cfg.position == "bottom_left":
            x = padding
            y = self.screen_height - bg_height - padding
        elif overlay_cfg.position == "bottom_right":
            x = self.screen_width - bg_width - padding
            y = self.screen_height - bg_height - padding
        elif overlay_cfg.position == "top_left":
            x = padding
            y = padding
        else:  # top_right
            x = self.screen_width - bg_width - padding
            y = padding

        # Draw background
        self.screen.blit(bg_surface, (x, y))

        # Draw text
        text_y = y + padding
        for surf in text_surfaces:
            self.screen.blit(surf, (x + padding, text_y))
            text_y += surf.get_height()

    def _wrap_text(self, text: str, max_width_chars: int = 50) -> list:
        """Wrap text to fit within screen."""
        words = text.split()
        lines = []
        current_line = []
        current_length = 0

        for word in words:
            if current_length + len(word) + 1 <= max_width_chars:
                current_line.append(word)
                current_length += len(word) + 1
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
                current_length = len(word)

        if current_line:
            lines.append(" ".join(current_line))

        return lines

    def _render_clock(self) -> None:
        """Render clock display."""
        self.screen.fill((0, 0, 0))

        # Get current time
        now = datetime.now()
        time_str = now.strftime("%H:%M")
        date_str = now.strftime("%A, %B %d")

        # Render time
        time_surface = self._clock_font.render(time_str, True, (255, 255, 255))
        time_x = (self.screen_width - time_surface.get_width()) // 2
        time_y = (self.screen_height - time_surface.get_height()) // 2 - 50

        # Render date
        date_font = pygame.font.SysFont(None, 48)
        date_surface = date_font.render(date_str, True, (200, 200, 200))
        date_x = (self.screen_width - date_surface.get_width()) // 2
        date_y = time_y + time_surface.get_height() + 20

        self.screen.blit(time_surface, (time_x, time_y))
        self.screen.blit(date_surface, (date_x, date_y))

    def show_black(self) -> None:
        """Show black screen."""
        self.mode = DisplayMode.BLACK
        self._source_image = None

    def show_clock(self) -> None:
        """Show clock display."""
        self.mode = DisplayMode.CLOCK
        self._source_image = None

    def set_mode(self, mode: DisplayMode) -> None:
        """
        Set the display mode.

        Args:
            mode: DisplayMode to set.
        """
        if mode == DisplayMode.BLACK:
            self.show_black()
        elif mode == DisplayMode.CLOCK:
            self.show_clock()
        elif mode == DisplayMode.SLIDESHOW:
            self.mode = DisplayMode.SLIDESHOW

    def is_transition_complete(self) -> bool:
        """Check if current transition is complete."""
        return not self._transitioning

    def is_photo_duration_complete(self) -> bool:
        """Check if current photo has been displayed long enough."""
        if self.mode != DisplayMode.SLIDESHOW:
            return False
        elapsed = time.time() - self._kb_start_time
        return elapsed >= self._kb_duration

    def handle_events(self) -> list:
        """
        Process pygame events.

        Returns:
            List of event names that occurred.
        """
        events = []

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                events.append("quit")
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    events.append("quit")
                elif event.key == pygame.K_SPACE or event.key == pygame.K_RIGHT:
                    events.append("next")
                elif event.key == pygame.K_LEFT:
                    events.append("previous")
            elif event.type == pygame.MOUSEBUTTONDOWN:
                events.append("next")

        return events

    def cleanup(self) -> None:
        """Clean up pygame resources."""
        pygame.quit()

    @property
    def resolution(self) -> Tuple[int, int]:
        """Get current display resolution."""
        return (self.screen_width, self.screen_height)
