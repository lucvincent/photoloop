# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Remote input handler for PhotoLoop.
Supports Fire TV Remote and similar Bluetooth remotes via evdev.
"""

import logging
import select
import threading
import time
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    from evdev import InputDevice, ecodes, list_devices
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False
    logger.warning("evdev not available - remote control disabled")


class RemoteAction(Enum):
    """Actions that can be triggered by remote buttons."""
    NEXT = "next"
    PREVIOUS = "previous"
    TOGGLE_PAUSE = "toggle_pause"
    SELECT = "select"


# Key mappings for Fire TV Remote
# D-pad: UP=103, DOWN=108, LEFT=105, RIGHT=106
# Center/Select: KPENTER=96
FIRE_TV_KEY_MAP = {
    105: RemoteAction.PREVIOUS,    # LEFT
    106: RemoteAction.NEXT,        # RIGHT
    96: RemoteAction.TOGGLE_PAUSE, # CENTER (KPENTER)
    28: RemoteAction.TOGGLE_PAUSE, # ENTER (fallback)
}


class RemoteInputHandler:
    """
    Handles input from Bluetooth remotes via evdev.

    Detects compatible remotes and translates button presses to actions.
    """

    # Remote names to auto-detect
    SUPPORTED_REMOTES = [
        "Amazon Fire TV Remote",
        "Fire TV Remote",
    ]

    def __init__(
        self,
        action_callback: Optional[Callable[[RemoteAction], None]] = None,
        reconnect_callback: Optional[Callable[[], None]] = None
    ):
        """
        Initialize the remote input handler.

        Args:
            action_callback: Function to call when an action is triggered.
            reconnect_callback: Function to call when remote reconnects (useful for
                wake-on-reconnect since the button press that woke the remote is lost).
        """
        self._callback = action_callback
        self._reconnect_callback = reconnect_callback
        self._device: Optional["InputDevice"] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._pending_actions: list[RemoteAction] = []
        self._lock = threading.Lock()
        self._was_connected = False  # Track connection state for reconnect detection

    def find_remote(self) -> Optional[str]:
        """
        Find a compatible remote device.

        Returns:
            Device path if found, None otherwise.
        """
        if not EVDEV_AVAILABLE:
            return None

        try:
            for path in list_devices():
                try:
                    device = InputDevice(path)
                    if any(name in device.name for name in self.SUPPORTED_REMOTES):
                        logger.info(f"Found remote: {device.name} at {path}")
                        return path
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Error scanning for remotes: {e}")

        return None

    def start(self) -> bool:
        """
        Start listening for remote input.

        Starts a background thread that continuously looks for and connects
        to compatible remotes, automatically reconnecting if disconnected.

        Returns:
            True if successfully started, False otherwise.
        """
        if not EVDEV_AVAILABLE:
            logger.warning("evdev not available - cannot start remote handler")
            return False

        # Try to find remote initially
        device_path = self.find_remote()
        if device_path:
            try:
                self._device = InputDevice(device_path)
                self._was_connected = True  # Mark as connected (not a reconnect)
                logger.info(f"Remote input handler started for {self._device.name}")
            except Exception as e:
                logger.warning(f"Failed to open remote device: {e}")
                self._device = None
        else:
            logger.info("No remote found initially - will keep looking")

        # Start the input loop thread (will auto-reconnect)
        self._running = True
        self._thread = threading.Thread(target=self._input_loop, daemon=True)
        self._thread.start()
        logger.info("Remote input handler started (auto-reconnect enabled)")
        return True

    def stop(self) -> None:
        """Stop listening for remote input."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = None
        logger.info("Remote input handler stopped")

    def _input_loop(self) -> None:
        """Background thread that reads input events with auto-reconnect."""
        reconnect_delay = 2  # seconds between reconnection attempts (faster for wake detection)

        while self._running:
            # Ensure we have a valid device
            if self._device is None:
                if not self._try_reconnect():
                    time.sleep(reconnect_delay)
                    continue

            try:
                # Use select for non-blocking read with timeout
                r, _, _ = select.select([self._device], [], [], 0.5)
                if not r:
                    continue

                for event in self._device.read():
                    if event.type == ecodes.EV_KEY and event.value == 1:  # Key down
                        action = FIRE_TV_KEY_MAP.get(event.code)
                        if action:
                            logger.debug(f"Remote: {action.value} (key {event.code})")
                            self._handle_action(action)

            except OSError as e:
                # Device disconnected - close and try to reconnect
                logger.warning(f"Remote disconnected: {e}")
                self._close_device()
                # Will attempt reconnect on next loop iteration

            except Exception as e:
                logger.error(f"Remote input error: {e}")

    def _try_reconnect(self) -> bool:
        """Attempt to find and connect to a remote device."""
        device_path = self.find_remote()
        if not device_path:
            return False

        try:
            self._device = InputDevice(device_path)
            logger.info(f"Remote reconnected: {self._device.name} at {device_path}")

            # If this is a genuine reconnect (was connected before), call the callback.
            # This allows wake-on-reconnect since the button press that woke the
            # remote from sleep is lost by the time the device appears.
            if self._was_connected and self._reconnect_callback:
                logger.info("Remote reconnected after disconnect - triggering reconnect callback")
                try:
                    self._reconnect_callback()
                except Exception as e:
                    logger.error(f"Reconnect callback error: {e}")
            self._was_connected = True

            return True
        except Exception as e:
            logger.debug(f"Failed to reconnect to remote: {e}")
            return False

    def _close_device(self) -> None:
        """Safely close the current device."""
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None

    def _handle_action(self, action: RemoteAction) -> None:
        """Handle an action from the remote."""
        if self._callback:
            self._callback(action)
        else:
            # Queue the action for polling
            with self._lock:
                self._pending_actions.append(action)

    def poll_actions(self) -> list[RemoteAction]:
        """
        Get and clear pending actions.

        Returns:
            List of actions since last poll.
        """
        with self._lock:
            actions = self._pending_actions.copy()
            self._pending_actions.clear()
        return actions

    def is_connected(self) -> bool:
        """Check if a remote is currently connected."""
        return self._device is not None and self._running
