# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Remote input handler for PhotoLoop.
Supports Fire TV Remote and similar Bluetooth remotes via evdev.
"""

import logging
import select
import threading
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

    def __init__(self, action_callback: Optional[Callable[[RemoteAction], None]] = None):
        """
        Initialize the remote input handler.

        Args:
            action_callback: Function to call when an action is triggered.
        """
        self._callback = action_callback
        self._device: Optional["InputDevice"] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._pending_actions: list[RemoteAction] = []
        self._lock = threading.Lock()

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

        Returns:
            True if successfully started, False otherwise.
        """
        if not EVDEV_AVAILABLE:
            logger.warning("evdev not available - cannot start remote handler")
            return False

        device_path = self.find_remote()
        if not device_path:
            logger.info("No compatible remote found")
            return False

        try:
            self._device = InputDevice(device_path)
            self._running = True
            self._thread = threading.Thread(target=self._input_loop, daemon=True)
            self._thread.start()
            logger.info(f"Remote input handler started for {self._device.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to start remote handler: {e}")
            return False

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
        """Background thread that reads input events."""
        while self._running and self._device:
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
                # Device disconnected
                logger.warning(f"Remote disconnected: {e}")
                self._running = False
                break
            except Exception as e:
                logger.error(f"Remote input error: {e}")

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
