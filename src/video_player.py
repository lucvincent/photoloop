# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Video playback for PhotoLoop.
Uses ffpyplayer for video decoding and pygame for display.
"""

import logging
import threading
import time
from typing import Callable, Optional, Tuple

import pygame

logger = logging.getLogger(__name__)

# Try to import ffpyplayer
try:
    from ffpyplayer.player import MediaPlayer
    FFPYPLAYER_AVAILABLE = True
except ImportError:
    FFPYPLAYER_AVAILABLE = False
    logger.warning("ffpyplayer not available - video playback disabled")


class VideoPlayer:
    """
    Video player using ffpyplayer for decoding and pygame for display.
    """

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        on_complete: Optional[Callable[[], None]] = None
    ):
        """
        Initialize the video player.

        Args:
            screen_width: Display width.
            screen_height: Display height.
            on_complete: Callback when video finishes.
        """
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.on_complete = on_complete

        self._player: Optional["MediaPlayer"] = None
        self._playing = False
        self._current_path: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """Check if video playback is available."""
        return FFPYPLAYER_AVAILABLE

    def play(self, video_path: str) -> bool:
        """
        Start playing a video.

        Args:
            video_path: Path to the video file.

        Returns:
            True if playback started successfully.
        """
        if not FFPYPLAYER_AVAILABLE:
            logger.warning("Video playback not available")
            return False

        self.stop()

        with self._lock:
            try:
                # Create media player with audio
                self._player = MediaPlayer(
                    video_path,
                    ff_opts={
                        'paused': False,
                        'autoexit': True
                    }
                )
                self._playing = True
                self._current_path = video_path

                logger.info(f"Playing video: {video_path}")
                return True

            except Exception as e:
                logger.error(f"Failed to play video {video_path}: {e}")
                self._player = None
                self._playing = False
                return False

    def stop(self) -> None:
        """Stop the current video."""
        with self._lock:
            if self._player:
                try:
                    self._player.close_player()
                except Exception:
                    pass
                self._player = None
            self._playing = False
            self._current_path = None

    def get_frame(self) -> Optional[pygame.Surface]:
        """
        Get the current video frame as a pygame Surface.

        Returns:
            pygame.Surface or None if no frame available.
        """
        if not self._playing or not self._player:
            return None

        with self._lock:
            try:
                frame, val = self._player.get_frame()

                if val == 'eof':
                    # Video finished
                    self._playing = False
                    if self.on_complete:
                        self.on_complete()
                    return None

                if frame is None:
                    return None

                img, t = frame

                # Convert frame to pygame surface
                size = img.get_size()
                data = img.to_bytearray()[0]

                # Create surface from RGB data
                surface = pygame.image.frombuffer(data, size, 'RGB')

                # Scale to screen size
                scaled = pygame.transform.smoothscale(
                    surface,
                    (self.screen_width, self.screen_height)
                )

                return scaled

            except Exception as e:
                logger.debug(f"Error getting video frame: {e}")
                return None

    @property
    def is_playing(self) -> bool:
        """Check if video is currently playing."""
        return self._playing

    def pause(self) -> None:
        """Pause video playback."""
        if self._player and self._playing:
            try:
                self._player.set_pause(True)
            except Exception:
                pass

    def resume(self) -> None:
        """Resume video playback."""
        if self._player and self._playing:
            try:
                self._player.set_pause(False)
            except Exception:
                pass

    def get_duration(self) -> Optional[float]:
        """Get video duration in seconds."""
        if self._player:
            try:
                metadata = self._player.get_metadata()
                return metadata.get('duration')
            except Exception:
                pass
        return None

    def get_position(self) -> Optional[float]:
        """Get current playback position in seconds."""
        if self._player:
            try:
                return self._player.get_pts()
            except Exception:
                pass
        return None


class SimpleVideoPlayer:
    """
    Simplified video player that just extracts frames without ffpyplayer.
    Uses OpenCV as a fallback (if available).
    """

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        on_complete: Optional[Callable[[], None]] = None
    ):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.on_complete = on_complete

        self._cap = None
        self._playing = False
        self._fps = 30
        self._frame_time = 1.0 / 30
        self._last_frame_time = 0

        # Check for OpenCV
        try:
            import cv2
            self._cv2 = cv2
            self._available = True
        except ImportError:
            self._cv2 = None
            self._available = False
            logger.warning("OpenCV not available - video playback limited")

    @property
    def available(self) -> bool:
        return self._available

    def play(self, video_path: str) -> bool:
        if not self._available:
            return False

        self.stop()

        try:
            self._cap = self._cv2.VideoCapture(video_path)
            if not self._cap.isOpened():
                return False

            self._fps = self._cap.get(self._cv2.CAP_PROP_FPS) or 30
            self._frame_time = 1.0 / self._fps
            self._last_frame_time = time.time()
            self._playing = True

            logger.info(f"Playing video (OpenCV): {video_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to play video: {e}")
            return False

    def stop(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None
        self._playing = False

    def get_frame(self) -> Optional[pygame.Surface]:
        if not self._playing or not self._cap:
            return None

        # Respect frame rate
        now = time.time()
        if now - self._last_frame_time < self._frame_time:
            return None

        self._last_frame_time = now

        ret, frame = self._cap.read()
        if not ret:
            self._playing = False
            if self.on_complete:
                self.on_complete()
            return None

        # Convert BGR to RGB
        frame_rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)

        # Resize to screen
        frame_resized = self._cv2.resize(
            frame_rgb,
            (self.screen_width, self.screen_height)
        )

        # Convert to pygame surface
        surface = pygame.surfarray.make_surface(frame_resized.swapaxes(0, 1))
        return surface

    @property
    def is_playing(self) -> bool:
        return self._playing


def create_video_player(
    screen_width: int,
    screen_height: int,
    on_complete: Optional[Callable[[], None]] = None
) -> Optional[VideoPlayer]:
    """
    Create a video player with the best available backend.

    Args:
        screen_width: Display width.
        screen_height: Display height.
        on_complete: Callback when video finishes.

    Returns:
        VideoPlayer instance or None if video not supported.
    """
    # Try ffpyplayer first (best quality with audio)
    if FFPYPLAYER_AVAILABLE:
        return VideoPlayer(screen_width, screen_height, on_complete)

    # Fallback to OpenCV (no audio)
    simple = SimpleVideoPlayer(screen_width, screen_height, on_complete)
    if simple.available:
        logger.info("Using OpenCV for video playback (no audio)")
        return simple

    logger.warning("No video playback backend available")
    return None
