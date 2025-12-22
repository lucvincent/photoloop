"""
Display engine for PhotoLoop.
Uses SDL2's hardware-accelerated texture rendering for smooth transitions.
"""

import logging
import os
import time
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

import pygame
import pygame._sdl2 as sdl2
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
    Main display engine using SDL2's hardware-accelerated renderer.

    Uses textures instead of surfaces for GPU-accelerated rendering,
    which provides smooth transitions even at 4K resolution.
    """

    def __init__(self, config: PhotoLoopConfig):
        """
        Initialize the display with hardware-accelerated rendering.

        Args:
            config: PhotoLoop configuration.
        """
        self.config = config

        # Initialize pygame (needed for fonts and events)
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

        # Create SDL2 window and hardware-accelerated renderer
        windowed = os.environ.get("PHOTOLOOP_WINDOWED", "").lower() in ("1", "true", "yes")

        if windowed:
            logger.info("Running in windowed mode (PHOTOLOOP_WINDOWED set)")
            self._window = sdl2.Window(
                "PhotoLoop",
                size=(self.screen_width, self.screen_height)
            )
        else:
            self._window = sdl2.Window(
                "PhotoLoop",
                size=(self.screen_width, self.screen_height),
                fullscreen=True
            )

        # Create hardware-accelerated renderer with vsync
        self._renderer = sdl2.Renderer(self._window, accelerated=True, vsync=True)
        logger.info("Using hardware-accelerated SDL2 renderer")

        # Current state
        self.mode = DisplayMode.BLACK
        self._current_texture: Optional[sdl2.Texture] = None
        self._next_texture: Optional[sdl2.Texture] = None
        self._current_media: Optional[CachedMedia] = None
        self._current_params: Optional[DisplayParams] = None
        self._needs_redraw = True

        # Transition state
        self._transitioning = False
        self._transition_start = 0
        self._transition_type = TransitionType.SLIDE_LEFT

        # Ken Burns state
        self._kb_start_time = 0
        self._kb_duration = config.display.photo_duration_seconds
        self._source_texture: Optional[sdl2.Texture] = None
        self._kb_source_size: Tuple[int, int] = (0, 0)

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

        # For compatibility with old code
        self.screen = None  # Not used with texture rendering
        self.clock = pygame.time.Clock()
        self.target_fps = 30  # Can do 30fps with GPU acceleration

    def _init_fonts(self) -> None:
        """Initialize fonts for overlay and clock."""
        font_names = [
            "DejaVuSans",
            "FreeSans",
            "LiberationSans",
            "Arial",
            None
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

    def _pil_to_texture(self, pil_image: Image.Image) -> sdl2.Texture:
        """Convert PIL Image to SDL2 Texture (GPU-resident)."""
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")

        # Convert to pygame surface first
        mode = pil_image.mode
        size = pil_image.size
        data = pil_image.tobytes()
        surface = pygame.image.fromstring(data, size, mode)

        # Create texture from surface
        texture = sdl2.Texture.from_surface(self._renderer, surface)
        return texture

    def _surface_to_texture(self, surface: pygame.Surface) -> sdl2.Texture:
        """Convert pygame Surface to SDL2 Texture."""
        return sdl2.Texture.from_surface(self._renderer, surface)

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

        # For Ken Burns: pre-scale image with zoom headroom
        if self.config.ken_burns.enabled and params.ken_burns and params.crop_region:
            max_zoom = max(params.ken_burns.start_zoom, params.ken_burns.end_zoom)
            kb_scale = max_zoom * 1.05

            crop = params.crop_region
            img_w, img_h = pil_image.size
            left = int(crop.x * img_w)
            top = int(crop.y * img_h)
            right = int((crop.x + crop.width) * img_w)
            bottom = int((crop.y + crop.height) * img_h)
            cropped = pil_image.crop((left, top, right, bottom))

            target_w = int(self.screen_width * kb_scale)
            target_h = int(self.screen_height * kb_scale)
            source_image = cropped.resize(
                (target_w, target_h),
                Image.Resampling.BILINEAR
            )
            self._source_texture = self._pil_to_texture(source_image)
            self._kb_source_size = (target_w, target_h)

            # Get initial frame
            next_texture = self._get_kb_frame(params.ken_burns, 0.0)
        else:
            self._source_texture = None
            self._kb_source_size = (0, 0)
            frame = self._processor.prepare_image_for_display(
                media.local_path,
                params
            )
            next_texture = self._pil_to_texture(frame)

        if transition and self._current_texture is not None:
            self._start_transition(next_texture)
        else:
            self._current_texture = next_texture
            self._next_texture = None

        self._kb_start_time = time.time()
        self._kb_duration = self.config.display.photo_duration_seconds
        self._needs_redraw = True

    def _get_kb_frame(
        self,
        animation: "KenBurnsAnimation",
        progress: float
    ) -> sdl2.Texture:
        """
        Get Ken Burns frame as a texture.

        Note: For Ken Burns, we draw the source texture with calculated
        src/dst rects directly rather than creating a new texture each frame.
        """
        if self._source_texture is None:
            return self._current_texture

        # For now, just return the source texture
        # The actual Ken Burns animation is handled in _render_slideshow
        return self._source_texture

    def _ease_in_out(self, t: float) -> float:
        """Smooth ease-in-out for natural Ken Burns motion."""
        if t < 0.5:
            return 2 * t * t
        else:
            return 1 - pow(-2 * t + 2, 2) / 2

    def _start_transition(self, next_texture: sdl2.Texture) -> None:
        """Start a transition to a new texture."""
        self._next_texture = next_texture
        self._transitioning = True
        self._transition_start = time.time()

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
        events = self.handle_events()
        if "quit" in events:
            return False

        needs_animation = (
            self._transitioning or
            (self.mode == DisplayMode.SLIDESHOW and
             self.config.ken_burns.enabled and
             self._source_texture is not None)
        )

        if self.mode == DisplayMode.BLACK:
            if self._needs_redraw:
                self._renderer.draw_color = (0, 0, 0, 255)
                self._renderer.clear()
                self._renderer.present()
                self._needs_redraw = False
            time.sleep(0.1)

        elif self.mode == DisplayMode.CLOCK:
            self._render_clock()
            self._renderer.present()
            time.sleep(1.0)

        elif self.mode == DisplayMode.SLIDESHOW:
            if self._transitioning:
                self._render_transition()
                self._renderer.present()
                time.sleep(1.0 / 60)  # 60fps during transitions
            elif needs_animation:
                self._render_slideshow()
                self._renderer.present()
                time.sleep(1.0 / self.target_fps)
            else:
                if self._needs_redraw:
                    self._render_slideshow()
                    self._renderer.present()
                    self._needs_redraw = False
                time.sleep(0.1)

        return True

    def _render_slideshow(self) -> None:
        """Render the current slideshow frame."""
        self._renderer.draw_color = (0, 0, 0, 255)
        self._renderer.clear()

        if self._source_texture is not None and self._current_params is not None:
            # Ken Burns animation
            now = time.time()
            elapsed = now - self._kb_start_time
            progress = min(1.0, elapsed / self._kb_duration)

            if self.config.ken_burns.enabled and self._current_params.ken_burns:
                anim = self._current_params.ken_burns
                zoom = anim.start_zoom + (anim.end_zoom - anim.start_zoom) * progress
                eased = self._ease_in_out(progress)
                cx = anim.start_center[0] + (anim.end_center[0] - anim.start_center[0]) * eased
                cy = anim.start_center[1] + (anim.end_center[1] - anim.start_center[1]) * eased

                src_w, src_h = self._kb_source_size
                view_w = src_w / zoom
                view_h = src_h / zoom
                center_x = cx * src_w
                center_y = cy * src_h

                left = int(center_x - view_w / 2)
                top = int(center_y - view_h / 2)
                left = max(0, min(src_w - int(view_w), left))
                top = max(0, min(src_h - int(view_h), top))

                src_rect = pygame.Rect(left, top, int(view_w), int(view_h))
                dst_rect = pygame.Rect(0, 0, self.screen_width, self.screen_height)
                self._source_texture.draw(srcrect=src_rect, dstrect=dst_rect)
            else:
                self._source_texture.draw(dstrect=(0, 0, self.screen_width, self.screen_height))
        elif self._current_texture:
            self._current_texture.draw(dstrect=(0, 0, self.screen_width, self.screen_height))

        # Render overlay
        if self.config.overlay.enabled and self._current_media:
            self._render_overlay()

    def _render_transition(self) -> None:
        """Render transition between photos."""
        if self._current_texture is None or self._next_texture is None:
            self._transitioning = False
            return

        elapsed = (time.time() - self._transition_start) * 1000
        duration = self.config.display.transition_duration_ms
        progress = min(1.0, elapsed / duration)

        if progress >= 1.0:
            self._current_texture = self._next_texture
            self._next_texture = None
            self._transitioning = False
            self._renderer.draw_color = (0, 0, 0, 255)
            self._renderer.clear()
            self._current_texture.draw(dstrect=(0, 0, self.screen_width, self.screen_height))
            return

        self._renderer.draw_color = (0, 0, 0, 255)
        self._renderer.clear()

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
            self._next_texture.draw(dstrect=(0, 0, self.screen_width, self.screen_height))

    def _render_fade_transition(self, progress: float) -> None:
        """Render a fade transition using texture alpha."""
        # Draw current image at full opacity
        self._current_texture.draw(dstrect=(0, 0, self.screen_width, self.screen_height))

        # Draw next image with increasing alpha
        alpha = int(255 * progress)
        self._next_texture.alpha = alpha
        self._next_texture.draw(dstrect=(0, 0, self.screen_width, self.screen_height))
        self._next_texture.alpha = 255

    def _render_slide_transition(
        self,
        progress: float,
        direction: Tuple[int, int]
    ) -> None:
        """Render a slide transition."""
        dx, dy = direction
        w, h = self.screen_width, self.screen_height

        # Current image slides out
        current_x = int(dx * w * progress)
        current_y = int(dy * h * progress)

        # Next image slides in
        next_x = int(-dx * w * (1 - progress))
        next_y = int(-dy * h * (1 - progress))

        self._current_texture.draw(dstrect=(current_x, current_y, w, h))
        self._next_texture.draw(dstrect=(next_x, next_y, w, h))

    def _render_overlay(self) -> None:
        """Render the metadata overlay."""
        if not self._current_media:
            return

        overlay_cfg = self.config.overlay
        lines = []

        if overlay_cfg.show_date and self._current_media.exif_date:
            try:
                date = datetime.fromisoformat(self._current_media.exif_date)
                date_str = format_date(date, overlay_cfg.date_format)
                if date_str:
                    lines.append(date_str)
            except Exception:
                pass

        if overlay_cfg.show_caption and self._current_media.caption:
            caption = self._current_media.caption
            if overlay_cfg.max_caption_length > 0:
                caption = caption[:overlay_cfg.max_caption_length]
                if len(self._current_media.caption) > overlay_cfg.max_caption_length:
                    caption += "..."
            lines.append(caption)

        if not lines:
            return

        # Render text to surfaces
        text_surfaces = []
        for line in lines:
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

        bg_width = max_width + padding * 2
        bg_height = total_height + padding * 2
        bg_color = tuple(overlay_cfg.background_color)

        # Create background surface with alpha
        bg_surface = pygame.Surface((bg_width, bg_height), pygame.SRCALPHA)
        bg_surface.fill(bg_color)

        # Draw text onto background
        text_y = padding
        for surf in text_surfaces:
            bg_surface.blit(surf, (padding, text_y))
            text_y += surf.get_height()

        # Convert to texture and draw
        overlay_texture = self._surface_to_texture(bg_surface)

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
        else:
            x = self.screen_width - bg_width - padding
            y = padding

        overlay_texture.draw(dstrect=(x, y, bg_width, bg_height))

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
        self._renderer.draw_color = (0, 0, 0, 255)
        self._renderer.clear()

        now = datetime.now()
        time_str = now.strftime("%H:%M")
        date_str = now.strftime("%A, %B %d")

        # Render time
        time_surface = self._clock_font.render(time_str, True, (255, 255, 255))
        time_texture = self._surface_to_texture(time_surface)
        time_w, time_h = time_surface.get_size()
        time_x = (self.screen_width - time_w) // 2
        time_y = (self.screen_height - time_h) // 2 - 50
        time_texture.draw(dstrect=(time_x, time_y, time_w, time_h))

        # Render date
        date_font = pygame.font.SysFont(None, 48)
        date_surface = date_font.render(date_str, True, (200, 200, 200))
        date_texture = self._surface_to_texture(date_surface)
        date_w, date_h = date_surface.get_size()
        date_x = (self.screen_width - date_w) // 2
        date_y = time_y + time_h + 20
        date_texture.draw(dstrect=(date_x, date_y, date_w, date_h))

    def show_black(self) -> None:
        """Show black screen."""
        if self.mode != DisplayMode.BLACK:
            self._needs_redraw = True
        self.mode = DisplayMode.BLACK
        self._source_texture = None

    def show_clock(self) -> None:
        """Show clock display."""
        if self.mode != DisplayMode.CLOCK:
            self._needs_redraw = True
        self.mode = DisplayMode.CLOCK
        self._source_texture = None

    def set_mode(self, mode: DisplayMode) -> None:
        """Set the display mode."""
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
        """Process pygame events."""
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
        """Clean up SDL2 and pygame resources."""
        self._current_texture = None
        self._next_texture = None
        self._source_texture = None
        del self._renderer
        del self._window
        pygame.quit()

    @property
    def resolution(self) -> Tuple[int, int]:
        """Get current display resolution."""
        return (self.screen_width, self.screen_height)
