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
from .image_processor import DisplayParams, ImageProcessor, KenBurnsAnimation
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
        self._needs_redraw = True  # Flag to track when display needs updating

        # Transition state
        self._transitioning = False
        self._transition_start = 0
        self._transition_type = TransitionType.FADE

        # Ken Burns state
        self._kb_start_time = 0
        self._kb_duration = config.display.photo_duration_seconds
        self._source_image: Optional[Image.Image] = None
        self._source_surface: Optional[pygame.Surface] = None  # Pre-scaled for Ken Burns
        self._kb_last_update = 0  # Last Ken Burns frame update time
        self._kb_update_interval = 1.0 / 15  # Ken Burns at 15fps (smooth enough for slow motion)

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

        # Clock - 10fps is enough for slow Ken Burns pan/zoom, saves significant CPU
        self.clock = pygame.time.Clock()
        self.target_fps = 10

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

        # Load and prepare source image
        try:
            pil_image = Image.open(media.local_path)
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")
        except Exception as e:
            logger.error(f"Failed to load image {media.local_path}: {e}")
            return

        # For Ken Burns: pre-scale image to a size suitable for real-time transforms
        # We need extra pixels for zoom headroom (max zoom is typically 1.15)
        if self.config.ken_burns.enabled and params.ken_burns and params.crop_region:
            max_zoom = max(params.ken_burns.start_zoom, params.ken_burns.end_zoom)
            # Scale factor: minimal headroom to reduce transform cost
            # At 4K, every extra pixel costs CPU
            kb_scale = max_zoom * 1.05  # Just 5% margin

            # Apply crop region first, then scale up
            crop = params.crop_region
            img_w, img_h = pil_image.size
            left = int(crop.x * img_w)
            top = int(crop.y * img_h)
            right = int((crop.x + crop.width) * img_w)
            bottom = int((crop.y + crop.height) * img_h)
            cropped = pil_image.crop((left, top, right, bottom))

            # Scale to just slightly larger than screen for zoom headroom
            # Use NEAREST for maximum speed (quality loss acceptable for motion)
            target_w = int(self.screen_width * kb_scale)
            target_h = int(self.screen_height * kb_scale)
            self._source_image = cropped.resize(
                (target_w, target_h),
                Image.Resampling.NEAREST  # Fastest resampling
            )
            self._source_surface = self._pil_to_pygame(self._source_image)

            # Get initial frame using fast pygame scaling
            next_surface = self._get_kb_frame_fast(params.ken_burns, 0.0)
        else:
            # No Ken Burns - just prepare static image
            self._source_image = None
            self._source_surface = None
            frame = self._processor.prepare_image_for_display(
                media.local_path,
                params
            )
            next_surface = self._pil_to_pygame(frame)

        if transition and self._current_surface is not None:
            self._start_transition(next_surface)
        else:
            self._current_surface = next_surface
            self._next_surface = None

        self._kb_start_time = time.time()
        self._kb_last_update = 0  # Reset to force immediate first frame
        self._kb_duration = self.config.display.photo_duration_seconds
        self._needs_redraw = True  # Force redraw for new photo

    def _get_kb_frame_fast(
        self,
        animation: "KenBurnsAnimation",
        progress: float
    ) -> pygame.Surface:
        """
        Get Ken Burns frame using fast pygame transforms.

        Args:
            animation: Ken Burns animation parameters.
            progress: Animation progress (0-1).

        Returns:
            pygame Surface for this frame.
        """
        if self._source_surface is None:
            return self._current_surface

        # Interpolate zoom
        zoom = animation.start_zoom + (animation.end_zoom - animation.start_zoom) * progress

        # Interpolate center with easing
        eased = self._ease_in_out(progress)
        cx = animation.start_center[0] + (animation.end_center[0] - animation.start_center[0]) * eased
        cy = animation.start_center[1] + (animation.end_center[1] - animation.start_center[1]) * eased

        # Source surface dimensions
        src_w = self._source_surface.get_width()
        src_h = self._source_surface.get_height()

        # Calculate the region to extract (inverse of zoom)
        # At zoom=1.0, we show the full source (which is already cropped)
        # At zoom=1.15, we show less of the source (more zoomed in)
        view_w = src_w / zoom
        view_h = src_h / zoom

        # Pan offset (normalized center to pixel coords)
        # cx, cy are in 0-1 range relative to original crop region
        # Map to source surface coordinates
        center_x = cx * src_w
        center_y = cy * src_h

        # Calculate subsurface rect
        left = int(center_x - view_w / 2)
        top = int(center_y - view_h / 2)

        # Clamp to valid range
        left = max(0, min(src_w - int(view_w), left))
        top = max(0, min(src_h - int(view_h), top))

        # Extract subsurface and scale to screen
        try:
            rect = pygame.Rect(left, top, int(view_w), int(view_h))
            subsurface = self._source_surface.subsurface(rect)
            # Use scale (faster than smoothscale, acceptable quality for video-like motion)
            return pygame.transform.scale(
                subsurface,
                (self.screen_width, self.screen_height)
            )
        except (ValueError, pygame.error) as e:
            # Fallback if subsurface fails
            logger.debug(f"Ken Burns subsurface error: {e}")
            return pygame.transform.scale(
                self._source_surface,
                (self.screen_width, self.screen_height)
            )

    def _ease_in_out(self, t: float) -> float:
        """Smooth ease-in-out for natural Ken Burns motion."""
        if t < 0.5:
            return 2 * t * t
        else:
            return 1 - pow(-2 * t + 2, 2) / 2

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

        # Check if we need to animate (Ken Burns or transition)
        needs_animation = (
            self._transitioning or
            (self.mode == DisplayMode.SLIDESHOW and
             self.config.ken_burns.enabled and
             self._source_surface is not None)
        )

        if self.mode == DisplayMode.BLACK:
            if self._needs_redraw:
                self.screen.fill((0, 0, 0))
                pygame.display.flip()
                self._needs_redraw = False
            # Sleep longer for static black screen
            time.sleep(0.1)

        elif self.mode == DisplayMode.CLOCK:
            # Clock updates every second
            self._render_clock()
            pygame.display.flip()
            time.sleep(1.0)  # Update once per second

        elif self.mode == DisplayMode.SLIDESHOW:
            if self._transitioning:
                self._render_transition()
                pygame.display.flip()
                time.sleep(1.0 / 30)  # 30fps for smooth transitions
            elif needs_animation:
                # Ken Burns animation
                self._render_slideshow()
                pygame.display.flip()
                time.sleep(1.0 / self.target_fps)
            else:
                # Static image - only redraw when needed
                if self._needs_redraw:
                    self._render_slideshow()
                    pygame.display.flip()
                    self._needs_redraw = False
                # Sleep longer for static images
                time.sleep(0.1)

        return True

    def _render_slideshow(self) -> None:
        """Render the current slideshow frame."""
        # If no Ken Burns source, just blit the static surface (no animation needed)
        if self._source_surface is None or self._current_params is None:
            if self._current_surface:
                self.screen.blit(self._current_surface, (0, 0))
            # Render overlay
            if self.config.overlay.enabled and self._current_media:
                self._render_overlay()
            return

        # For Ken Burns: animate the frame
        now = time.time()
        elapsed = now - self._kb_start_time
        progress = min(1.0, elapsed / self._kb_duration)

        if self.config.ken_burns.enabled and self._current_params.ken_burns:
            self._current_surface = self._get_kb_frame_fast(
                self._current_params.ken_burns,
                progress
            )

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
        if self.mode != DisplayMode.BLACK:
            self._needs_redraw = True
        self.mode = DisplayMode.BLACK
        self._source_image = None
        self._source_surface = None

    def show_clock(self) -> None:
        """Show clock display."""
        if self.mode != DisplayMode.CLOCK:
            self._needs_redraw = True
        self.mode = DisplayMode.CLOCK
        self._source_image = None
        self._source_surface = None

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
            if self.mode != DisplayMode.SLIDESHOW:
                self._needs_redraw = True
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
