# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Display engine for PhotoLoop.
Uses SDL2's hardware-accelerated texture rendering for smooth transitions.
"""

import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from enum import Enum
from typing import Callable, Optional, Tuple

import pygame
import pygame._sdl2 as sdl2
from PIL import Image

from .cache_manager import CachedMedia
from .config import PhotoLoopConfig, OverlayConfig
from .image_processor import DisplayParams, ImageProcessor, KenBurnsAnimation
from .metadata import format_date, reverse_geocode

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

        # Create SDL2 window and hardware-accelerated renderer
        windowed = os.environ.get("PHOTOLOOP_WINDOWED", "").lower() in ("1", "true", "yes")

        if windowed:
            # For windowed mode, use configured or default resolution
            if config.display.resolution == "auto":
                info = pygame.display.Info()
                self.screen_width = info.current_w
                self.screen_height = info.current_h
            else:
                parts = config.display.resolution.lower().split('x')
                self.screen_width = int(parts[0])
                self.screen_height = int(parts[1])

            logger.info("Running in windowed mode (PHOTOLOOP_WINDOWED set)")
            self._window = sdl2.Window(
                "PhotoLoop",
                size=(self.screen_width, self.screen_height)
            )
        else:
            # For fullscreen: create window first, then query actual size
            # This avoids issues where pygame.display.Info() returns wrong
            # resolution before display is fully initialized (common on Pi)
            if config.display.resolution == "auto":
                # Create fullscreen window - SDL2 will use native resolution
                self._window = sdl2.Window(
                    "PhotoLoop",
                    size=(1920, 1080),  # Initial size, will be overridden by fullscreen
                    fullscreen=True
                )
                # Query actual window size AFTER fullscreen is set
                self.screen_width, self.screen_height = self._window.size
            else:
                parts = config.display.resolution.lower().split('x')
                self.screen_width = int(parts[0])
                self.screen_height = int(parts[1])
                self._window = sdl2.Window(
                    "PhotoLoop",
                    size=(self.screen_width, self.screen_height),
                    fullscreen=True
                )

        logger.info(f"Display resolution: {self.screen_width}x{self.screen_height}")

        # Create hardware-accelerated renderer with vsync
        self._renderer = sdl2.Renderer(self._window, accelerated=True, vsync=True)
        logger.info("Using hardware-accelerated SDL2 renderer")

        # Hide cursor - use SDL2's native cursor API for Wayland compatibility
        # On Wayland, pygame's cursor methods don't work reliably.
        # We create a transparent cursor using SDL2's low-level API.
        self._hide_cursor()

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
        self._transition_frames = 0
        self._transition_duration_override: Optional[int] = None  # For fast manual nav

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
            smart_crop_method=config.scaling.smart_crop_method,
            face_position=config.scaling.face_position,
            fallback_crop=config.scaling.fallback_crop,
            max_crop_percent=config.scaling.max_crop_percent,
            saliency_threshold=config.scaling.saliency_threshold,
            saliency_coverage=config.scaling.saliency_coverage,
            crop_bias=config.scaling.crop_bias,
            background_color=tuple(config.scaling.background_color),
            ken_burns_enabled=config.ken_burns.enabled,
            ken_burns_zoom_range=tuple(config.ken_burns.zoom_range),
            ken_burns_pan_speed=config.ken_burns.pan_speed,
            ken_burns_randomize=config.ken_burns.randomize
        )

        # Background color for letterbox/pillarbox bars
        self._bg_color = tuple(config.scaling.background_color) + (255,)

        # Fonts for overlay and clock
        self._init_fonts()

        # For compatibility with old code
        self.screen = None  # Not used with texture rendering
        self.clock = pygame.time.Clock()
        self.target_fps = 30  # Can do 30fps with GPU acceleration

        # Track display power state
        self._display_powered = True

        # Photo control state
        self._paused = False
        self._skip_requested = False
        self._previous_requested = False

        # Lazy geocoding state
        self._location_update_callback: Optional[Callable[[str, str], None]] = None
        self._geocoding_in_progress: Optional[str] = None  # media_id being geocoded

        # Visual feedback state
        self._feedback_type = None  # 'paused', 'resuming', 'next', 'previous'
        self._feedback_start_time = 0
        self._feedback_duration = 0  # 0 = persistent (for paused)

        # Cached scaled fonts for feedback (avoid creating fonts every frame)
        self._feedback_text_font = None
        self._paused_indicator_font = None
        self._init_feedback_fonts()

        # Verify display dimensions match native resolution
        # SDL2 on Wayland sometimes reports wrong initial size (e.g., 1080p on 4K display)
        self._refresh_display_dimensions()

    def _hide_cursor(self) -> None:
        """Hide the mouse cursor.

        On Wayland/labwc, we use the compositor's HideCursor action via a keybinding.
        The keybinding (Super+F12) is configured in ~/.config/labwc/rc.xml and
        triggered via wtype. This warps the cursor off-screen and hides it until
        mouse movement occurs (keyboard/remote input won't unhide it).

        Falls back to pygame.mouse.set_visible(False) for X11.
        """
        # Try labwc HideCursor action first (works on Wayland)
        try:
            result = subprocess.run(
                ["wtype", "-M", "logo", "-k", "F12"],
                capture_output=True,
                timeout=2
            )
            if result.returncode == 0:
                logger.debug("Cursor hidden via labwc HideCursor action")
                return
        except FileNotFoundError:
            pass  # wtype not installed, try fallback
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.debug(f"labwc cursor hiding failed: {e}")

        # Fallback for X11 or other display servers
        try:
            pygame.mouse.set_visible(False)
        except Exception:
            pass

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
        self._feedback_font = None
        self._feedback_icon_font = None

        for font_name in font_names:
            try:
                self._overlay_font = pygame.font.SysFont(
                    font_name,
                    self.config.overlay.font_size
                )
                self._clock_font = pygame.font.SysFont(font_name, 120)
                self._feedback_font = pygame.font.SysFont(font_name, 48)
                self._feedback_icon_font = pygame.font.SysFont(font_name, 96)
                break
            except Exception:
                continue

        if self._overlay_font is None:
            self._overlay_font = pygame.font.Font(None, self.config.overlay.font_size)
        if self._clock_font is None:
            self._clock_font = pygame.font.Font(None, 120)
        if self._feedback_font is None:
            self._feedback_font = pygame.font.Font(None, 48)
        if self._feedback_icon_font is None:
            self._feedback_icon_font = pygame.font.Font(None, 96)

    def reload_fonts(self) -> None:
        """Reload fonts when config changes (e.g., font size updated)."""
        logger.info(f"Reloading fonts (font_size={self.config.overlay.font_size})")
        self._init_fonts()
        self._init_feedback_fonts()
        self._needs_redraw = True  # Force redraw to show new font size

    def _init_feedback_fonts(self) -> None:
        """Initialize resolution-scaled fonts for feedback overlays.

        These fonts are cached to avoid expensive font creation every frame.
        Call this when screen resolution changes.
        """
        res_scale = self.screen_height / 1080.0
        try:
            self._feedback_text_font = pygame.font.SysFont(None, int(54 * res_scale))
            self._paused_indicator_font = pygame.font.SysFont(None, int(34 * res_scale))
        except Exception:
            # Fallback to default fonts
            self._feedback_text_font = pygame.font.Font(None, int(54 * res_scale))
            self._paused_indicator_font = pygame.font.Font(None, int(34 * res_scale))

    def set_location_update_callback(
        self,
        callback: Callable[[str, str], None]
    ) -> None:
        """
        Set callback for persisting location updates to cache.

        Args:
            callback: Function(media_id, location) to persist geocoded location.
        """
        self._location_update_callback = callback

    def notify_metadata_updated(self, media_id: str) -> None:
        """
        Notify display that metadata was updated for a specific media item.

        If the media_id matches the currently displayed photo, triggers a redraw
        so the overlay reflects the new caption/location/date immediately.

        This allows incremental caption updates during sync - as each caption is
        fetched, it becomes visible on the currently displayed photo without
        waiting for the full sync to complete.

        Args:
            media_id: The media ID that was updated.
        """
        if self._current_media and self._current_media.media_id == media_id:
            logger.debug(f"Metadata updated for current photo {media_id}, triggering redraw")
            self._needs_redraw = True

    def _lazy_geocode_if_needed(self) -> None:
        """
        Trigger background geocoding for current photo if it has GPS but no location.

        This is called after show_photo() sets _current_media. If the photo has
        GPS coordinates but no location string, we geocode in a background thread
        and update both the overlay and the cache when complete.

        Only triggers if the location would actually be displayed (overlay enabled,
        captions shown, and exif_location is a configured caption source).
        """
        if not self._current_media:
            return

        # Only geocode if location would be displayed
        overlay_cfg = self.config.overlay
        if not overlay_cfg.enabled or not overlay_cfg.show_caption:
            return

        # Check if exif_location is configured as a caption source
        caption_sources = getattr(overlay_cfg, 'caption_sources', {})
        if "exif_location" not in caption_sources:
            return

        media = self._current_media

        # Skip if already has location or no GPS coordinates
        if media.location:
            return
        if not media.gps_latitude or not media.gps_longitude:
            return

        # Skip if already geocoding this photo
        if self._geocoding_in_progress == media.media_id:
            return

        # Mark as in progress
        self._geocoding_in_progress = media.media_id
        media_id = media.media_id
        lat = media.gps_latitude
        lon = media.gps_longitude

        def do_geocode():
            """Background thread to perform reverse geocoding."""
            try:
                location = reverse_geocode(lat, lon)
                if location:
                    logger.info(f"Lazy geocoded {media_id}: {location}")

                    # Update current media if it's still being displayed
                    if (self._current_media and
                            self._current_media.media_id == media_id):
                        self._current_media.location = location
                        self._needs_redraw = True  # Trigger overlay update

                    # Persist to cache via callback
                    if self._location_update_callback:
                        self._location_update_callback(media_id, location)
                else:
                    logger.debug(f"Geocoding returned no result for {media_id}")
            except Exception as e:
                logger.debug(f"Lazy geocoding failed for {media_id}: {e}")
            finally:
                # Clear in-progress flag if still set to this media
                if self._geocoding_in_progress == media_id:
                    self._geocoding_in_progress = None

        # Start background thread
        thread = threading.Thread(target=do_geocode, daemon=True)
        thread.start()

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

        # Enable alpha blending for fade transitions
        # blend_mode: 0=none, 1=blend, 2=add, 4=mod
        texture.blend_mode = 1

        return texture

    def _surface_to_texture(self, surface: pygame.Surface) -> sdl2.Texture:
        """Convert pygame Surface to SDL2 Texture."""
        texture = sdl2.Texture.from_surface(self._renderer, surface)
        texture.blend_mode = 1  # Enable alpha blending
        return texture

    def show_photo(
        self,
        media: CachedMedia,
        params: DisplayParams,
        transition: bool = True,
        manual_nav: bool = False
    ) -> None:
        """
        Display a photo with optional transition.

        Args:
            media: Cached media to display.
            params: Display parameters.
            transition: Whether to use transition effect.
            manual_nav: If True, this is manual navigation (next/prev button).
                       Uses faster transition for snappier feel.

        Note: Display wake is handled by _wake_display_if_needed() - the single
        place for DPMS wake logic. Called here right before displaying content.
        """
        # Wake display right before showing content (avoids empty screen delay)
        self._wake_display_if_needed()

        self.mode = DisplayMode.SLIDESHOW
        self._current_media = media
        self._current_params = params

        logger.info(f"show_photo: screen={self.screen_width}x{self.screen_height}, kb_enabled={self.config.ken_burns.enabled}, params.kb={params.ken_burns is not None}")

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

            # Calculate target size maintaining aspect ratio
            # The cropped image may have different aspect ratio than screen
            # (e.g., panoramas in balanced mode)
            cropped_aspect = cropped.width / cropped.height
            screen_aspect = self.screen_width / self.screen_height

            # Target dimensions with kb_scale headroom
            base_w = int(self.screen_width * kb_scale)
            base_h = int(self.screen_height * kb_scale)

            if cropped_aspect > screen_aspect:
                # Cropped image is wider than screen - fit by height
                # This ensures we have enough vertical headroom for Ken Burns
                target_h = base_h
                target_w = int(target_h * cropped_aspect)
            else:
                # Cropped image is taller than screen - fit by width
                target_w = base_w
                target_h = int(target_w / cropped_aspect)

            # Cap texture dimensions to SDL2's 4096 limit
            max_texture_size = 4096
            if target_w > max_texture_size or target_h > max_texture_size:
                scale_down = min(max_texture_size / target_w, max_texture_size / target_h)
                target_w = int(target_w * scale_down)
                target_h = int(target_h * scale_down)
                logger.debug(f"Capped Ken Burns texture to {target_w}x{target_h}")

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
            logger.info(f"show_photo: frame size={frame.size}, params.resolution={params.screen_resolution}")
            next_texture = self._pil_to_texture(frame)

        if transition and self._current_texture is not None:
            self._start_transition(next_texture, fast=manual_nav)
        else:
            # First photo (no transition) - need special handling to ensure
            # renderer is fully synchronized with GPU before texture is visible.
            # This fixes the quarter-screen issue on some Wayland compositors.
            self._current_texture = next_texture
            self._next_texture = None

            # Force multiple render passes to prime the GPU pipeline
            # The first render may use stale buffer state on some systems
            logger.info("First photo - priming GPU with multiple renders")
            for _ in range(3):
                self._render_slideshow()
                self._renderer.present()
                time.sleep(0.016)  # ~60fps timing

        self._kb_start_time = time.time()
        self._kb_duration = self.config.display.photo_duration_seconds
        self._needs_redraw = True

        # Trigger lazy geocoding if photo has GPS but no location
        self._lazy_geocode_if_needed()

    def show_preloaded_photo(
        self,
        media: CachedMedia,
        params: DisplayParams,
        frame: Image.Image,
        transition: bool = True,
        manual_nav: bool = False
    ) -> None:
        """
        Display a pre-loaded photo (skips loading/processing).

        This is the fast path for pre-loaded photos - the frame is already
        processed and just needs texture creation.

        Args:
            media: Cached media to display.
            params: Display parameters.
            frame: Pre-processed PIL Image ready for texture.
            transition: Whether to use transition effect.
            manual_nav: If True, use faster transition.

        Note: Display wake is handled by _wake_display_if_needed() - the single
        place for DPMS wake logic. Called here right before displaying content.
        """
        # Wake display right before showing content (avoids empty screen delay)
        self._wake_display_if_needed()

        self.mode = DisplayMode.SLIDESHOW
        self._current_media = media
        self._current_params = params

        logger.info(f"show_preloaded_photo: using pre-loaded frame {frame.size}")

        # Create texture from pre-loaded frame (fast - just texture creation)
        next_texture = self._pil_to_texture(frame)

        if transition and self._current_texture is not None:
            self._start_transition(next_texture, fast=manual_nav)
        else:
            self._current_texture = next_texture
            self._next_texture = None
            # First photo priming
            logger.info("First photo - priming GPU with multiple renders")
            for _ in range(3):
                self._render_slideshow()
                self._renderer.present()
                time.sleep(0.016)

        self._kb_start_time = time.time()
        self._kb_duration = self.config.display.photo_duration_seconds
        self._needs_redraw = True
        self._lazy_geocode_if_needed()

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

    def _start_transition(self, next_texture: sdl2.Texture, fast: bool = False) -> None:
        """Start a transition to a new texture.

        Args:
            next_texture: The texture to transition to.
            fast: If True, use a faster transition (for manual navigation).
        """
        self._next_texture = next_texture
        self._transitioning = True
        self._transition_start = time.time()
        self._transition_frames = 0
        # For manual navigation, use a much faster transition (300ms vs 1000ms)
        self._transition_duration_override = 300 if fast else None

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

        # Check if feedback animation is active (needs continuous rendering for fade)
        feedback_animating = (
            self._feedback_type is not None and
            self._feedback_duration > 0
        )

        needs_animation = (
            self._transitioning or
            feedback_animating or
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
                # vsync handles timing, no sleep needed
                return True  # Continue animation loop
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
        self._renderer.draw_color = self._bg_color
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
                src_aspect = src_w / src_h
                screen_aspect = self.screen_width / self.screen_height

                # Calculate view size to match SCREEN aspect ratio (not source)
                # This ensures no stretching when drawing to screen
                if src_aspect > screen_aspect:
                    # Source is wider - fit by height, view is narrower than source
                    view_h = src_h / zoom
                    view_w = view_h * screen_aspect
                else:
                    # Source is taller - fit by width, view is shorter than source
                    view_w = src_w / zoom
                    view_h = view_w / screen_aspect

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
            logger.info(f"_render_slideshow: drawing at dstrect=(0, 0, {self.screen_width}, {self.screen_height})")
            self._current_texture.draw(dstrect=(0, 0, self.screen_width, self.screen_height))

        # Render overlay
        if self.config.overlay.enabled and self._current_media:
            self._render_overlay()

        # Render feedback overlay (paused, next/prev arrows, etc.)
        self._render_feedback()

        # Render persistent paused indicator (bottom-right, after main feedback fades)
        self._render_paused_indicator()

    def _render_transition(self) -> None:
        """Render transition between photos."""
        if self._current_texture is None or self._next_texture is None:
            self._transitioning = False
            return

        elapsed = (time.time() - self._transition_start) * 1000
        # Use override duration for manual navigation, otherwise config
        duration = self._transition_duration_override or self.config.display.transition_duration_ms
        progress = min(1.0, elapsed / duration)

        self._transition_frames += 1

        if progress >= 1.0:
            # Transition complete - swap textures
            self._current_texture = self._next_texture
            self._next_texture = None
            self._transitioning = False
            self._needs_redraw = True  # Ensure next update() renders via _render_slideshow()

            # Don't render here - let update() loop handle it via _render_slideshow()
            # This ensures the SAME code path as the working first-photo case,
            # which fixes the quarter-screen bug after DPMS wake.
            return

        self._renderer.draw_color = self._bg_color
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

        # Render feedback overlay on top of transition
        self._render_feedback()

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

    def _build_caption(self, overlay_cfg: OverlayConfig) -> Optional[str]:
        """
        Build caption string from available sources based on priority settings.

        Sources are sorted by priority (lower number = higher priority).
        Returns up to max_caption_sources, joined by caption_separator.
        """
        if not self._current_media:
            return None

        # Get caption source priorities (default if not configured)
        source_priorities = getattr(overlay_cfg, 'caption_sources', {
            "google_caption": 1,
            "embedded_caption": 2,
            "google_location": 3
        })
        max_sources = getattr(overlay_cfg, 'max_caption_sources', 1)
        separator = getattr(overlay_cfg, 'caption_separator', " — ")

        # Placeholder values to filter out
        invalid_values = {'unknown location', 'add location', 'add a description'}

        # Camera info patterns to filter out (some cameras put this in description fields)
        camera_info_patterns = [
            'DIGITAL CAMERA', 'DIGITAL PHOTO', 'CAMERA PHONE',
            # Common camera brands that appear as standalone "captions"
            'OLYMPUS', 'FUJIFILM', 'FUJI', 'CANON', 'NIKON', 'SONY',
            'SAMSUNG', 'PANASONIC', 'KODAK', 'LEICA', 'PENTAX', 'RICOH',
            'APPLE', 'IPHONE',
        ]

        def is_camera_info(caption: str) -> bool:
            """Check if caption looks like auto-generated camera info."""
            cap_upper = caption.upper().strip()
            for pattern in camera_info_patterns:
                if pattern in cap_upper:
                    return True
            return False

        # Collect available caption values with their priorities
        # Track seen values to skip duplicates (case-insensitive)
        available = []
        seen_values = set()

        def add_if_unique(priority: int, value: str) -> None:
            """Add value if not a duplicate (case-insensitive comparison)."""
            normalized = value.lower().strip()
            if normalized not in seen_values:
                seen_values.add(normalized)
                available.append((priority, value))

        # Google caption/description from Google Photos DOM
        if "google_caption" in source_priorities:
            value = self._current_media.google_caption
            if value and value.lower() not in invalid_values and not is_camera_info(value):
                add_if_unique(source_priorities["google_caption"], value)

        # Embedded EXIF/IPTC caption from photo file
        if "embedded_caption" in source_priorities:
            value = self._current_media.embedded_caption
            if value and value.lower() not in invalid_values and not is_camera_info(value):
                add_if_unique(source_priorities["embedded_caption"], value)

        # Google location from Google Photos DOM
        if "google_location" in source_priorities:
            value = self._current_media.google_location
            if value and value.lower() not in invalid_values:
                add_if_unique(source_priorities["google_location"], value)

        # EXIF GPS location (reverse-geocoded)
        if "exif_location" in source_priorities:
            value = self._current_media.location
            if value and value.lower() not in invalid_values:
                add_if_unique(source_priorities["exif_location"], value)

        if not available:
            return None

        # Sort by priority (lower number = higher priority)
        available.sort(key=lambda x: x[0])

        # Take top N sources
        selected = [value for _, value in available[:max_sources]]

        # Join with separator
        return separator.join(selected) if selected else None

    def _render_overlay(self) -> None:
        """Render the metadata overlay."""
        if not self._current_media:
            return

        overlay_cfg = self.config.overlay
        lines = []

        if overlay_cfg.show_date:
            # Use EXIF date first, fall back to Google Photos date if EXIF unavailable
            # Never show download date - only actual photo dates
            date_source = self._current_media.exif_date or self._current_media.google_date
            if date_source:
                try:
                    date = datetime.fromisoformat(date_source)
                    date_str = format_date(date, overlay_cfg.date_format)
                    if date_str:
                        lines.append(date_str)
                except Exception:
                    pass

        if overlay_cfg.show_caption:
            # Build caption from available sources based on priority
            # Lower priority number = higher priority (shown first)
            caption = self._build_caption(overlay_cfg)
            if caption:
                if overlay_cfg.max_caption_length > 0:
                    original_len = len(caption)
                    caption = caption[:overlay_cfg.max_caption_length]
                    if original_len > overlay_cfg.max_caption_length:
                        caption += "..."
                # Split on newlines to support multi-line captions (e.g., caption + location)
                lines.extend(caption.split('\n'))

        if not lines:
            return

        # Render text to surfaces
        # Calculate max characters to fit within screen width with some margin
        # Approximate character width is ~0.6 * font_size for most fonts
        char_width_approx = overlay_cfg.font_size * 0.6
        max_overlay_width = min(self.screen_width - 2 * overlay_cfg.padding, 3800)  # Cap at 3800 to stay under 4096
        max_chars = max(20, int(max_overlay_width / char_width_approx))

        text_surfaces = []
        for line in lines:
            wrapped = self._wrap_text(line, max_chars)
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

        # Safety cap: ensure overlay doesn't exceed SDL2 texture limits
        max_texture_size = 4000  # Leave margin under 4096
        if bg_width > max_texture_size or bg_height > max_texture_size:
            logger.warning(f"Overlay too large ({bg_width}x{bg_height}), capping to {max_texture_size}")
            bg_width = min(bg_width, max_texture_size)
            bg_height = min(bg_height, max_texture_size)

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

    def _recreate_renderer(self) -> None:
        """
        Recreate the SDL2 renderer to ensure clean state.

        This is needed after display power cycles, as the GPU/compositor
        state may become inconsistent on some systems (especially Wayland).
        """
        logger.info("Recreating SDL2 renderer after display power cycle")

        # Clear any existing textures (they'll be invalid after renderer recreation)
        self._current_texture = None
        self._next_texture = None
        self._source_texture = None

        # Delete old renderer
        try:
            del self._renderer
        except Exception as e:
            logger.warning(f"Error deleting old renderer: {e}")

        # Query current window size
        self.screen_width, self.screen_height = self._window.size
        logger.info(f"Window size after power cycle: {self.screen_width}x{self.screen_height}")

        # Create new renderer
        self._renderer = sdl2.Renderer(self._window, accelerated=True, vsync=True)

        # Reset viewport to full window size (critical for correct rendering)
        try:
            # Explicitly set viewport to full window dimensions
            full_rect = pygame.Rect(0, 0, self.screen_width, self.screen_height)
            self._renderer.set_viewport(full_rect)
            logger.info(f"Set viewport to {full_rect}")
        except Exception as e:
            logger.warning(f"Could not reset viewport: {e}")

        # Log new renderer state
        try:
            viewport = self._renderer.get_viewport()
            logical = self._renderer.logical_size
            scale = self._renderer.scale
            logger.info(
                f"New renderer state - viewport: {viewport}, "
                f"logical: {logical}, scale: {scale}"
            )
        except Exception as e:
            logger.debug(f"Could not get new renderer state: {e}")

        # Update image processor if dimensions changed
        self._processor = ImageProcessor(
            screen_width=self.screen_width,
            screen_height=self.screen_height,
            scaling_mode=self.config.scaling.mode,
            smart_crop_method=self.config.scaling.smart_crop_method,
            face_position=self.config.scaling.face_position,
            fallback_crop=self.config.scaling.fallback_crop,
            max_crop_percent=self.config.scaling.max_crop_percent,
            saliency_threshold=self.config.scaling.saliency_threshold,
            saliency_coverage=self.config.scaling.saliency_coverage,
            crop_bias=self.config.scaling.crop_bias,
            background_color=tuple(self.config.scaling.background_color),
            ken_burns_enabled=self.config.ken_burns.enabled,
            ken_burns_zoom_range=tuple(self.config.ken_burns.zoom_range),
            ken_burns_pan_speed=self.config.ken_burns.pan_speed,
            ken_burns_randomize=self.config.ken_burns.randomize
        )

        logger.info("Renderer recreated successfully")

    def _get_display_resolution(self) -> Optional[Tuple[int, int]]:
        """
        Get the native display resolution from wlr-randr.

        Returns:
            Tuple of (width, height) or None if unavailable.
        """
        try:
            env = os.environ.copy()
            env['XDG_RUNTIME_DIR'] = '/run/user/1000'
            env['WAYLAND_DISPLAY'] = 'wayland-0'

            result = subprocess.run(
                ['wlr-randr'],
                capture_output=True,
                text=True,
                timeout=5,
                env=env
            )

            if result.returncode == 0:
                # Look for the "current" mode line
                for line in result.stdout.split('\n'):
                    if 'current' in line and 'px' in line:
                        # Parse "3840x2160 px, 60.000000 Hz (preferred, current)"
                        parts = line.strip().split()
                        if parts:
                            resolution = parts[0]  # "3840x2160"
                            w, h = resolution.split('x')
                            return (int(w), int(h))
        except Exception as e:
            logger.debug(f"Could not get display resolution: {e}")

        return None

    def _refresh_display_dimensions(self, force_recreate: bool = False) -> None:
        """
        Re-query window dimensions and update if changed.

        This is needed after display power on, as some display managers
        may alter window state during power transitions. On Wayland with
        DPMS, SDL2 may report stale dimensions after the display wakes.

        Args:
            force_recreate: If True, always recreate the renderer even if
                          dimensions appear unchanged. Needed after DPMS wake
                          where GPU state can be corrupted.
        """
        logger.debug(f"Refreshing display dimensions (force_recreate={force_recreate})")
        new_width, new_height = self._window.size

        # Log current renderer state for debugging
        try:
            viewport = self._renderer.get_viewport()
            logical = self._renderer.logical_size
            scale = self._renderer.scale
            logger.info(
                f"Renderer state - window: {new_width}x{new_height}, "
                f"viewport: {viewport}, logical: {logical}, scale: {scale}"
            )
        except Exception as e:
            logger.debug(f"Could not get renderer state: {e}")

        # Get the actual display resolution from wlr-randr
        # This is more reliable than SDL2's window size after DPMS wake
        native_res = self._get_display_resolution()

        # Check if SDL2's window dimensions differ significantly from native
        # Common symptom after DPMS wake: window reports half its expected size
        needs_refresh = False

        if native_res:
            native_w, native_h = native_res
            # Check for significant mismatch (more than 10% different)
            if (abs(new_width - native_w) > native_w * 0.1 or
                    abs(new_height - native_h) > native_h * 0.1):
                logger.warning(
                    f"Window size mismatch: SDL2 reports {new_width}x{new_height}, "
                    f"but display is {native_w}x{native_h}"
                )
                needs_refresh = True
        else:
            # Fallback: check for suspiciously small dimensions
            min_expected_width = 800
            if new_width < min_expected_width or new_height < min_expected_width:
                logger.warning(
                    f"Window dimensions look wrong ({new_width}x{new_height})"
                )
                needs_refresh = True

        if needs_refresh:
            logger.info("Toggling fullscreen to refresh window dimensions")
            try:
                # Toggle fullscreen off then on to force SDL2 to re-query display
                self._window.set_fullscreen(False)
                time.sleep(0.1)
                self._window.set_fullscreen(True)
                time.sleep(0.2)  # Give compositor time to stabilize
                new_width, new_height = self._window.size
                logger.info(f"After fullscreen toggle: {new_width}x{new_height}")

                # Verify the fix worked
                if native_res:
                    native_w, native_h = native_res
                    if new_width != native_w or new_height != native_h:
                        logger.warning(
                            f"Fullscreen toggle didn't fully fix resolution: "
                            f"got {new_width}x{new_height}, expected {native_w}x{native_h}"
                        )
            except Exception as e:
                logger.error(f"Failed to toggle fullscreen: {e}")

        # Always reset the viewport to full window size
        # This fixes issues where the renderer's viewport gets corrupted
        # Note: set_viewport(None) doesn't always work, so use explicit rect
        try:
            full_rect = pygame.Rect(0, 0, new_width, new_height)
            self._renderer.set_viewport(full_rect)
            logger.info(f"Reset viewport to {full_rect}")
        except Exception as e:
            logger.warning(f"Could not reset viewport: {e}")

        # Reset logical size if it's set (should be None for 1:1 pixel mapping)
        try:
            if self._renderer.logical_size != (0, 0):
                # logical_size of (0,0) means "use window size"
                # We can't set it directly to None, but we can try to clear it
                logger.warning(f"Renderer has non-default logical size: {self._renderer.logical_size}")
        except Exception as e:
            logger.debug(f"Could not check logical size: {e}")

        dimensions_changed = (new_width != self.screen_width or
                               new_height != self.screen_height)

        if dimensions_changed:
            logger.warning(
                f"Display dimensions changed: {self.screen_width}x{self.screen_height} -> "
                f"{new_width}x{new_height}"
            )
            self.screen_width = new_width
            self.screen_height = new_height

            # Update image processor with new dimensions
            self._processor = ImageProcessor(
                screen_width=self.screen_width,
                screen_height=self.screen_height,
                scaling_mode=self.config.scaling.mode,
                smart_crop_method=self.config.scaling.smart_crop_method,
                face_position=self.config.scaling.face_position,
                fallback_crop=self.config.scaling.fallback_crop,
                max_crop_percent=self.config.scaling.max_crop_percent,
                saliency_threshold=self.config.scaling.saliency_threshold,
                saliency_coverage=self.config.scaling.saliency_coverage,
                crop_bias=self.config.scaling.crop_bias,
                background_color=tuple(self.config.scaling.background_color),
                ken_burns_enabled=self.config.ken_burns.enabled,
                ken_burns_zoom_range=tuple(self.config.ken_burns.zoom_range),
                ken_burns_pan_speed=self.config.ken_burns.pan_speed,
                ken_burns_randomize=self.config.ken_burns.randomize
            )

        # Recreate renderer if dimensions changed OR if forced (e.g., after DPMS wake)
        # DPMS can corrupt GPU state even when dimensions appear unchanged
        if dimensions_changed or force_recreate:
            if force_recreate and not dimensions_changed:
                logger.info("Force recreating renderer after DPMS wake")
            self._recreate_renderer()

            # Reinit scaled feedback fonts for new resolution
            self._init_feedback_fonts()

            # Brief delay for compositor to stabilize after renderer recreation
            time.sleep(0.1)

            logger.info(f"Display dimensions: {self.screen_width}x{self.screen_height}")

    def _get_wayland_output(self) -> Optional[str]:
        """
        Get the Wayland output name (e.g., HDMI-A-1) using wlr-randr.

        Returns:
            Output name string, or None if not available.
        """
        try:
            env = os.environ.copy()
            env['XDG_RUNTIME_DIR'] = '/run/user/1000'
            env['WAYLAND_DISPLAY'] = 'wayland-0'

            result = subprocess.run(
                ['wlr-randr'],
                capture_output=True,
                text=True,
                timeout=5,
                env=env
            )

            if result.returncode == 0:
                # Parse first line to get output name (e.g., "HDMI-A-1 \"LG...\"")
                first_line = result.stdout.strip().split('\n')[0]
                output_name = first_line.split()[0]
                return output_name
        except Exception as e:
            logger.debug(f"Could not get Wayland output: {e}")

        return None

    def _try_wlopm(self, on: bool, output_name: str) -> bool:
        """Try wlopm (Wayland DPMS) power control. Returns True if successful."""
        try:
            env = os.environ.copy()
            env['XDG_RUNTIME_DIR'] = '/run/user/1000'
            env['WAYLAND_DISPLAY'] = 'wayland-0'

            action = '--on' if on else '--off'
            result = subprocess.run(
                ['wlopm', action, output_name],
                capture_output=True,
                text=True,
                timeout=10,
                env=env
            )

            if result.returncode == 0:
                logger.info(f"Display power {'on' if on else 'off'} (wlopm DPMS: {output_name})")
                return True
            else:
                logger.debug(f"wlopm failed: {result.stderr}")
        except FileNotFoundError:
            logger.debug("wlopm not available")
        except subprocess.TimeoutExpired:
            logger.debug("wlopm timed out")
        except Exception as e:
            logger.debug(f"wlopm error: {e}")
        return False

    def _try_cec(self, on: bool) -> bool:
        """Try HDMI-CEC power control. Returns True if successful."""
        try:
            if on:
                result = subprocess.run(
                    ['cec-client', '-s', '-d', '1'],
                    input='on 0\n',
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            else:
                result = subprocess.run(
                    ['cec-client', '-s', '-d', '1'],
                    input='standby 0\n',
                    capture_output=True,
                    text=True,
                    timeout=10
                )

            if result.returncode == 0:
                logger.info(f"Display power {'on' if on else 'off'} (CEC)")
                return True
            else:
                logger.debug(f"cec-client failed: {result.stderr}")
        except FileNotFoundError:
            logger.debug("cec-client not available")
        except subprocess.TimeoutExpired:
            logger.debug("cec-client timed out")
        except Exception as e:
            logger.debug(f"CEC error: {e}")
        return False

    def _set_display_power(self, on: bool) -> None:
        """
        Control physical display power.

        The method used depends on config.display.power_control:
        - "auto": try wlopm (DPMS), then HDMI-CEC, then black screen
        - "wlopm": Wayland DPMS only (for monitors on labwc/Wayland)
        - "cec": HDMI-CEC only (for TVs)
        - "none": just show black screen, don't control display power

        Args:
            on: True to turn display on, False to turn off.
        """
        logger.debug(f"Setting display power: {on} (current: {self._display_powered})")
        self._display_powered = on

        power_method = self.config.display.power_control.lower()
        output_name = self._get_wayland_output()

        # Method: none - just show black screen
        if power_method == "none":
            if on:
                logger.info("Display resumed (power_control=none)")
            else:
                logger.info("Display off-hours mode (power_control=none, showing black)")
            return

        # Method: wlopm - Wayland DPMS only
        if power_method == "wlopm":
            if output_name and self._try_wlopm(on, output_name):
                return
            # wlopm failed or not available
            if on:
                logger.warning("wlopm failed, display may not have woken")
            else:
                logger.warning("wlopm failed, showing black screen instead")
            return

        # Method: cec - HDMI-CEC only
        if power_method == "cec":
            if self._try_cec(on):
                return
            # CEC failed or not available
            if on:
                logger.warning("CEC failed, display may not have woken")
            else:
                logger.warning("CEC failed, showing black screen instead")
            return

        # Method: auto - try wlopm first, then CEC, then fallback
        if output_name and self._try_wlopm(on, output_name):
            return

        if self._try_cec(on):
            return

        # Fallback: just log (black screen still saves some power on many displays)
        if on:
            logger.info("Display resumed (no hardware power control available)")
        else:
            logger.info("Display off-hours mode (showing black screen)")

    def show_black(self) -> None:
        """Show black screen and turn off display to save power."""
        logger.debug(f"show_black() called, mode={self.mode.value}, display_powered={self._display_powered}")
        if self.mode != DisplayMode.BLACK:
            self._needs_redraw = True
        self.mode = DisplayMode.BLACK
        self._source_texture = None

        # Turn off physical display to save electricity
        if self._display_powered:
            logger.debug("show_black: turning off display power")
            self._set_display_power(False)

    def show_clock(self) -> None:
        """Show clock display.

        Note: This is now only called internally. External callers should use
        set_mode(DisplayMode.CLOCK) which handles display power management.
        """
        if self.mode != DisplayMode.CLOCK:
            self._needs_redraw = True
        self.mode = DisplayMode.CLOCK
        self._source_texture = None

    def set_mode(self, mode: DisplayMode) -> None:
        """Set the display mode.

        Note: This only sets the mode flag. Display wake from DPMS happens in
        show_photo()/show_preloaded_photo() right before content is displayed,
        to avoid showing empty screen while loading.
        """
        logger.debug(f"set_mode({mode.value}) called, current={self.mode.value}")
        if mode == DisplayMode.BLACK:
            self.show_black()
        elif mode == DisplayMode.CLOCK:
            # CLOCK mode means off_hours_mode='clock' - display stays on during off-hours
            # No wake needed since we never go BLACK → CLOCK
            if self.mode != DisplayMode.CLOCK:
                self._needs_redraw = True
            self.mode = DisplayMode.CLOCK
            self._source_texture = None
        elif mode == DisplayMode.SLIDESHOW:
            # Just set mode - wake happens in show_photo()/show_preloaded_photo()
            if self.mode != DisplayMode.SLIDESHOW:
                self._needs_redraw = True
            self.mode = DisplayMode.SLIDESHOW

    def _wake_display_if_needed(self) -> None:
        """Wake display from DPMS standby if powered off.

        This is the SINGLE place where display wake + renderer recreation happens.
        Called by show_photo() and show_preloaded_photo() right before displaying.

        TODO: Desktop may be visible for several seconds during wake sequence.
        The SDL2 window loses foreground/fullscreen state during DPMS standby.
        Attempted fix (fullscreen toggle before renderer recreation) caused the
        quarter-screen bug to return. Need to find a way to bring window to
        foreground without disrupting renderer state. Possible approaches:
        - Fullscreen toggle AFTER burn-in completes
        - Use wlrctl or labwc IPC to raise window
        - Render to a secondary buffer and flip
        """
        if not self._display_powered:
            logger.info("Waking display from DPMS standby")

            # Turn display on first
            self._set_display_power(True)

            # Brief delay for display to physically wake
            time.sleep(0.3)

            # Recreate renderer - GPU state may be corrupted after DPMS
            logger.info("Recreating renderer after DPMS wake")
            self._refresh_display_dimensions(force_recreate=True)

            # GPU burn-in: Render black frames to stabilize compositor buffers
            # This fixes the quarter-screen bug by forcing compositor to commit
            # buffer sizes before we render actual content. Without this, the
            # compositor may still be adjusting during photo display, causing
            # photos to render in only the top-left quarter of the screen.
            logger.info("GPU burn-in after DPMS wake")
            burn_in_frames = 45  # ~1.5 seconds at 30fps
            for _ in range(burn_in_frames):
                self._renderer.draw_color = self._bg_color
                self._renderer.clear()
                self._renderer.present()
                time.sleep(1.0 / 30)
            logger.info("GPU burn-in complete")

    def is_transition_complete(self) -> bool:
        """Check if current transition is complete."""
        return not self._transitioning

    def is_photo_duration_complete(self) -> bool:
        """Check if current photo has been displayed long enough."""
        if self.mode != DisplayMode.SLIDESHOW:
            return False
        # Paused means never complete
        if self._paused:
            return False
        elapsed = time.time() - self._kb_start_time
        return elapsed >= self._kb_duration

    def skip_to_next(self) -> None:
        """Request skip to next photo."""
        self._skip_requested = True
        self._previous_requested = False
        self._show_feedback('next', duration=1.0)  # Base duration, extended on cache miss
        logger.info("Skip to next requested, feedback shown")

    def skip_to_previous(self) -> None:
        """Request skip to previous photo."""
        self._previous_requested = True
        self._skip_requested = True  # Also set skip to trigger immediate transition
        self._show_feedback('previous', duration=1.0)  # Base duration, extended on cache miss
        logger.info("Skip to previous requested, feedback shown")

    def is_skip_requested(self) -> bool:
        """Check and clear skip (next) request flag."""
        if self._skip_requested:
            self._skip_requested = False
            return True
        return False

    def is_previous_requested(self) -> bool:
        """Check and clear previous request flag."""
        if self._previous_requested:
            self._previous_requested = False
            return True
        return False

    def pause(self) -> None:
        """Pause the slideshow on the current photo."""
        self._paused = True
        self._show_feedback('paused', duration=1.0)
        logger.info("Slideshow paused")

    def resume(self) -> None:
        """Resume the slideshow auto-advance."""
        self._paused = False
        self._show_feedback('resuming', duration=1.0)
        # Reset the timer so we get a full duration on this photo
        self._kb_start_time = time.time()
        logger.info("Slideshow resumed")

    def toggle_pause(self) -> bool:
        """Toggle pause state. Returns new paused state."""
        if self._paused:
            self.resume()
        else:
            self.pause()
        return self._paused

    def is_paused(self) -> bool:
        """Check if slideshow is paused."""
        return self._paused

    def _show_feedback(self, feedback_type: str, duration: float = 1.0) -> None:
        """Show visual feedback overlay.

        Args:
            feedback_type: 'paused', 'resuming', 'next', or 'previous'
            duration: How long to show (0 = persistent until cleared)
        """
        self._feedback_type = feedback_type
        self._feedback_start_time = time.time()
        self._feedback_duration = duration
        self._needs_redraw = True

        # Force immediate render to show feedback without delay
        # Note: This is called from remote input thread, so we must be careful
        # to use the appropriate render path based on current state
        if self.mode == DisplayMode.SLIDESHOW and self._current_texture:
            try:
                if self._transitioning and self._next_texture:
                    self._render_transition()
                else:
                    self._render_slideshow()
                self._renderer.present()
            except Exception as e:
                # Rendering from non-main thread can fail - main loop will retry
                logger.debug(f"Immediate feedback render failed: {e}")

    def extend_feedback_duration(self, additional_seconds: float) -> None:
        """Extend current feedback overlay duration (e.g., for cache miss).

        Args:
            additional_seconds: Additional time to add to current duration
        """
        if self._feedback_type is not None and self._feedback_duration > 0:
            self._feedback_duration += additional_seconds
            logger.debug(f"Extended feedback duration by {additional_seconds}s to {self._feedback_duration}s")

    def _render_feedback(self) -> None:
        """Render visual feedback overlay for user actions."""
        if self._feedback_type is None:
            return

        logger.debug(f"Rendering feedback: type={self._feedback_type}, duration={self._feedback_duration}")

        # Check if temporary feedback has expired
        if self._feedback_duration > 0:
            elapsed = time.time() - self._feedback_start_time
            if elapsed >= self._feedback_duration:
                self._feedback_type = None
                # Trigger redraw to show persistent indicator if paused
                if self._paused:
                    self._needs_redraw = True
                return
            # Calculate fade-out alpha for last 0.3 seconds
            fade_start = self._feedback_duration - 0.3
            if elapsed > fade_start:
                alpha = int(255 * (1 - (elapsed - fade_start) / 0.3))
            else:
                alpha = 255
        else:
            alpha = 255  # Persistent (paused state)

        # Define feedback content - use unicode symbols
        if self._feedback_type == 'paused':
            icon = "\u275A\u275A"  # ❚❚ (heavy vertical bars)
            text = "PAUSED"
        elif self._feedback_type == 'resuming':
            icon = "\u25B6"  # ▶ (play triangle)
            text = "RESUMING"
        elif self._feedback_type == 'next':
            icon = "\u25B6"  # ▶ (right arrow)
            text = None
        elif self._feedback_type == 'previous':
            icon = "\u25C0"  # ◀ (left arrow)
            text = None
        else:
            return

        # Render icon with large font
        try:
            icon_surface = self._feedback_icon_font.render(icon, True, (255, 255, 255))
        except Exception:
            # Fallback to ASCII if unicode fails
            fallback = {"paused": "||", "resuming": ">", "next": ">>", "previous": "<<"}
            icon_surface = self._feedback_icon_font.render(
                fallback.get(self._feedback_type, ">"),
                True, (255, 255, 255)
            )

        # Scale factor for resolution independence (1080p = 1.0, 4K = 2.0)
        res_scale = self.screen_height / 1080.0

        # Make icons larger to fill more of the container
        # Pause icon (double bars) needs to be slightly smaller to match arrow visual weight
        if self._feedback_type == 'paused':
            target_icon_height = int(120 * res_scale)  # Smaller for pause to match arrow visual weight
        else:
            target_icon_height = int(150 * res_scale)  # Arrows

        raw_w, raw_h = icon_surface.get_size()
        if raw_h > 0:
            scale = target_icon_height / raw_h
            new_w = int(raw_w * scale)
            new_h = target_icon_height
            icon_surface = pygame.transform.smoothscale(icon_surface, (new_w, new_h))

        icon_w, icon_h = icon_surface.get_size()

        # Container size scales with resolution for consistent physical appearance
        container_size = int(180 * res_scale)

        # Create background surface with rounded corners
        bg_surface = pygame.Surface((container_size, container_size), pygame.SRCALPHA)
        bg_color = (0, 0, 0, 120)  # Lighter background

        # Draw rounded rectangle
        radius = int(20 * res_scale)
        pygame.draw.rect(bg_surface, bg_color, (radius, 0, container_size - 2*radius, container_size))
        pygame.draw.rect(bg_surface, bg_color, (0, radius, container_size, container_size - 2*radius))
        pygame.draw.circle(bg_surface, bg_color, (radius, radius), radius)
        pygame.draw.circle(bg_surface, bg_color, (container_size - radius, radius), radius)
        pygame.draw.circle(bg_surface, bg_color, (radius, container_size - radius), radius)
        pygame.draw.circle(bg_surface, bg_color, (container_size - radius, container_size - radius), radius)

        # Get the actual bounding box of the icon (trim transparent edges)
        # This ensures perfect visual centering regardless of font metrics
        icon_rect = icon_surface.get_bounding_rect()

        # Calculate position to center the visible part of the icon
        icon_x = (container_size - icon_rect.width) // 2 - icon_rect.x
        icon_y = (container_size - icon_rect.height) // 2 - icon_rect.y
        bg_surface.blit(icon_surface, (icon_x, icon_y))

        # Position container on screen (center)
        container_x = (self.screen_width - container_size) // 2
        container_y = (self.screen_height - container_size) // 2

        logger.debug(f"Feedback overlay: type={self._feedback_type}, container={container_size}x{container_size}, pos=({container_x},{container_y}), alpha={alpha}")

        # Convert to texture and draw with alpha
        feedback_texture = self._surface_to_texture(bg_surface)
        feedback_texture.alpha = alpha  # Set texture alpha for fade effect
        feedback_texture.draw(dstrect=(container_x, container_y, container_size, container_size))

        # Render text below container if present (no background)
        if text:
            # Use cached scaled font (avoid creating fonts every frame)
            text_surface = self._feedback_text_font.render(text, True, (255, 255, 255))
            text_surface.set_alpha(alpha)
            text_w, text_h = text_surface.get_size()
            text_x = (self.screen_width - text_w) // 2
            text_y = container_y + container_size + int(15 * res_scale)  # Scaled gap below container

            # Convert text to texture and draw
            text_texture = self._surface_to_texture(text_surface)
            text_texture.alpha = alpha
            text_texture.draw(dstrect=(text_x, text_y, text_w, text_h))

    def _render_paused_indicator(self) -> None:
        """Render subtle persistent 'Paused' indicator in bottom-right corner."""
        if not self._paused:
            return

        # Don't show if the main feedback overlay is still visible
        if self._feedback_type is not None:
            if self._feedback_duration == 0:
                logger.debug("Paused indicator: skipped (persistent feedback active)")
                return  # Persistent feedback active
            elapsed = time.time() - self._feedback_start_time
            if elapsed < self._feedback_duration:
                logger.debug(f"Paused indicator: skipped (feedback active, {elapsed:.1f}s elapsed)")
                return  # Temporary feedback still showing

        logger.debug("Rendering paused indicator")

        # Scale factor for resolution independence (1080p = 1.0, 4K = 2.0)
        res_scale = self.screen_height / 1080.0

        # Render "PAUSED" text in amber/orange color
        # Amber color indicates "waiting/hold" state
        text = "PAUSED"
        amber_color = (255, 191, 0)  # Amber/gold color
        # Use cached scaled font (avoid creating fonts every frame)
        text_surface = self._paused_indicator_font.render(text, True, amber_color)
        text_w, text_h = text_surface.get_size()

        # Small padding (scaled)
        padding = int(10 * res_scale)
        bg_w = text_w + padding * 2
        bg_h = text_h + padding * 2

        # Semi-transparent dark background with slight amber tint
        bg_surface = pygame.Surface((bg_w, bg_h), pygame.SRCALPHA)
        bg_color = (40, 30, 0, 140)  # Dark with slight amber tint

        # Simple rounded rectangle
        radius = min(int(8 * res_scale), bg_h // 4)
        pygame.draw.rect(bg_surface, bg_color, (radius, 0, bg_w - 2*radius, bg_h))
        pygame.draw.rect(bg_surface, bg_color, (0, radius, bg_w, bg_h - 2*radius))
        pygame.draw.circle(bg_surface, bg_color, (radius, radius), radius)
        pygame.draw.circle(bg_surface, bg_color, (bg_w - radius, radius), radius)
        pygame.draw.circle(bg_surface, bg_color, (radius, bg_h - radius), radius)
        pygame.draw.circle(bg_surface, bg_color, (bg_w - radius, bg_h - radius), radius)

        # Blit text centered
        text_x = padding
        text_y = padding
        bg_surface.blit(text_surface, (text_x, text_y))

        # Position in bottom-right corner with margin (scaled)
        margin = int(20 * res_scale)
        x = self.screen_width - bg_w - margin
        y = self.screen_height - bg_h - margin

        # Convert to texture and draw
        indicator_texture = self._surface_to_texture(bg_surface)
        indicator_texture.draw(dstrect=(x, y, bg_w, bg_h))

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
        # Ensure display is turned back on before exit
        if not self._display_powered:
            self._set_display_power(True)

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
