#!/usr/bin/env python3
# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
PhotoLoop - Main Application.
Orchestrates all components: display, caching, scheduling, and web interface.
"""

import argparse
from datetime import datetime, timedelta
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Initialize logging early
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def setup_file_logging(log_dir: str) -> None:
    """Set up file logging in addition to console."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path / 'photoloop.log')
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logging.getLogger().addHandler(file_handler)


class PhotoLoop:
    """Main PhotoLoop application."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize PhotoLoop.

        Args:
            config_path: Path to configuration file.
        """
        self.config_path = config_path
        self.config = None
        self.cache_manager = None
        self.scheduler = None
        self.display = None
        self.remote_handler = None
        self.web_thread = None
        self.sync_thread = None

        self._running = False
        self._shutdown_event = threading.Event()

        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating shutdown...")
        self.stop()

    def _load_config(self) -> bool:
        """Load configuration."""
        from .config import load_config, validate_config, DEFAULT_CONFIG_PATHS

        try:
            # Find config file
            if self.config_path:
                config_path = self.config_path
            else:
                # Search default locations
                config_path = None
                for path in DEFAULT_CONFIG_PATHS:
                    if os.path.exists(path):
                        config_path = path
                        break

                if not config_path:
                    # Use default config
                    logger.warning("No config file found, using defaults")
                    config_path = None

            self.config = load_config(config_path)

            # Validate
            errors = validate_config(self.config)
            if errors:
                for error in errors:
                    logger.warning(f"Config warning: {error}")

            logger.info(f"Configuration loaded from: {self.config.config_path or 'defaults'}")
            return True

        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            return False

    def _init_cache_manager(self) -> bool:
        """Initialize the cache manager."""
        from .cache_manager import CacheManager

        try:
            self.cache_manager = CacheManager(self.config)
            logger.info(f"Cache manager initialized: {self.config.cache.directory}")

            # Log cache stats
            counts = self.cache_manager.get_media_count()
            size = self.cache_manager.get_cache_size_mb()
            logger.info(f"Cache contains {counts['photos']} photos, {counts['videos']} videos ({size:.1f} MB)")

            return True

        except Exception as e:
            logger.error(f"Failed to initialize cache manager: {e}")
            return False

    def _init_scheduler(self) -> bool:
        """Initialize the scheduler."""
        from .scheduler import Scheduler

        try:
            self.scheduler = Scheduler(self.config)
            status = self.scheduler.get_status()
            logger.info(f"Scheduler initialized: {status['state']}")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize scheduler: {e}")
            return False

    def _init_display(self) -> bool:
        """Initialize the display engine."""
        from .display import Display

        try:
            self.display = Display(self.config)

            # Set up lazy geocoding callback if cache manager is available
            if self.cache_manager:
                self.display.set_location_update_callback(
                    self.cache_manager.update_location
                )

            logger.info("Display engine initialized")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize display: {e}")
            return False

    def _init_remote(self) -> None:
        """Initialize the remote input handler (optional)."""
        from .remote_input import RemoteInputHandler, RemoteAction

        def handle_remote_action(action: RemoteAction):
            """Map remote actions to control requests."""
            action_map = {
                RemoteAction.NEXT: 'next',
                RemoteAction.PREVIOUS: 'prev',
                RemoteAction.TOGGLE_PAUSE: 'toggle_pause',
                RemoteAction.SELECT: 'toggle_pause',
            }
            control_action = action_map.get(action)
            if control_action:
                self._on_control_request(control_action)

        try:
            self.remote_handler = RemoteInputHandler(action_callback=handle_remote_action)
            if self.remote_handler.start():
                logger.info("Remote input handler started")
            else:
                logger.info("No remote detected (optional)")
                self.remote_handler = None
        except Exception as e:
            logger.debug(f"Remote input not available: {e}")
            self.remote_handler = None

    def _start_web_server(self) -> None:
        """Start the web server in a background thread."""
        if not self.config.web.enabled:
            logger.info("Web interface disabled")
            return

        from .web.app import create_app

        def run_web():
            app = create_app(
                self.config,
                cache_manager=self.cache_manager,
                scheduler=self.scheduler,
                display=self.display,
                on_config_change=self._on_config_change,
                on_sync_request=self._on_sync_request,
                on_control_request=self._on_control_request
            )

            # Disable Flask's default logging for cleaner output
            import logging as stdlib_logging
            stdlib_logging.getLogger('werkzeug').setLevel(stdlib_logging.WARNING)

            logger.info(f"Starting web server on {self.config.web.host}:{self.config.web.port}")

            try:
                app.run(
                    host=self.config.web.host,
                    port=self.config.web.port,
                    debug=False,
                    threaded=True,
                    use_reloader=False
                )
            except Exception as e:
                logger.error(f"Web server error: {e}")

        self.web_thread = threading.Thread(target=run_web, daemon=True)
        self.web_thread.start()

    def _format_interval(self, minutes: int) -> str:
        """Format minutes as human-readable string."""
        days, remainder = divmod(minutes, 1440)
        hours, mins = divmod(remainder, 60)
        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if mins > 0 or not parts:
            parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
        return ", ".join(parts)

    def _seconds_until_time(self, time_str: str) -> int:
        """Calculate seconds until a given time (HH:MM format).

        If the time has already passed today, returns seconds until that time tomorrow.
        """
        try:
            target_hour, target_minute = map(int, time_str.split(':'))
            now = datetime.now()
            target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

            # If target time has passed today, schedule for tomorrow
            if target <= now:
                target += timedelta(days=1)

            return int((target - now).total_seconds())
        except (ValueError, AttributeError):
            logger.warning(f"Invalid sync_time format: {time_str}, expected HH:MM")
            return 0

    def _start_sync_thread(self) -> None:
        """Start the background sync thread."""
        if self.config.sync.interval_minutes <= 0:
            logger.info("Automatic sync disabled (interval=0)")
            return

        def sync_loop():
            interval = self.config.sync.interval_minutes * 60
            interval_str = self._format_interval(self.config.sync.interval_minutes)
            first_sync_done = False

            # If sync_on_start is enabled, sync after a short delay
            if self.config.sync.sync_on_start:
                logger.info("Sync on start enabled, will sync in 30 seconds...")
                if self._shutdown_event.wait(30):
                    return  # Shutdown requested
                try:
                    logger.info("Starting sync-on-start...")
                    self.cache_manager.sync()
                    logger.info("Sync-on-start completed")
                except Exception as e:
                    logger.error(f"Sync-on-start error: {e}")

            # Wait for first scheduled sync time
            sync_time = self.config.sync.sync_time
            if sync_time:
                # Wait until the specified time for first scheduled sync
                wait_seconds = self._seconds_until_time(sync_time)
                if wait_seconds > 0:
                    wait_str = self._format_interval(wait_seconds // 60)
                    logger.info(f"Waiting until {sync_time} for first scheduled sync ({wait_str})")
                    if self._shutdown_event.wait(wait_seconds):
                        return  # Shutdown requested
            else:
                # No sync_time specified, wait for interval before first sync
                # (unless sync_on_start already did a sync)
                if not self.config.sync.sync_on_start:
                    logger.info(f"Waiting {interval_str} until first scheduled sync")
                    if self._shutdown_event.wait(interval):
                        return  # Shutdown requested

            while not self._shutdown_event.is_set():
                try:
                    logger.info("Starting scheduled album sync...")
                    self.cache_manager.sync()
                    logger.info("Scheduled sync completed")
                except Exception as e:
                    logger.error(f"Sync error: {e}")

                # Wait for next sync or shutdown
                if self._shutdown_event.wait(interval):
                    break

        self.sync_thread = threading.Thread(target=sync_loop, daemon=True)
        self.sync_thread.start()
        interval_str = self._format_interval(self.config.sync.interval_minutes)
        sync_time = self.config.sync.sync_time
        if sync_time:
            logger.info(f"Sync thread started (first sync at {sync_time}, then every {interval_str})")
        else:
            logger.info(f"Sync thread started (interval: {interval_str})")

    def _on_config_change(self) -> None:
        """Handle configuration changes from web interface."""
        logger.info("Configuration changed, reloading...")
        try:
            self._load_config()
            if self.scheduler:
                self.scheduler.config = self.config
            if self.display:
                self.display.config = self.config
                self.display.reload_fonts()
            if self.cache_manager:
                self.cache_manager.config = self.config
                # Rebuild playlist to reflect album enable/disable changes
                self.cache_manager.rebuild_playlist()
                logger.info("Playlist rebuilt after config change")
        except Exception as e:
            logger.error(f"Error reloading config: {e}")

    def _on_sync_request(
        self,
        update_all_captions: bool = False,
        force_refetch_captions: bool = False
    ) -> None:
        """Handle sync request from web interface."""
        def do_sync():
            try:
                if force_refetch_captions:
                    logger.info("Manual sync requested (force re-fetch ALL Google metadata)...")
                elif update_all_captions:
                    logger.info("Manual sync requested (fetch missing Google metadata)...")
                else:
                    logger.info("Manual sync requested...")
                self.cache_manager.sync(
                    update_all_captions=update_all_captions,
                    force_refetch_captions=force_refetch_captions
                )
                logger.info("Manual sync completed")
            except Exception as e:
                logger.error(f"Manual sync error: {e}")

        threading.Thread(target=do_sync, daemon=True).start()

    def _on_control_request(self, action: str) -> None:
        """Handle control request from web interface or CLI."""
        logger.info(f"Control request: {action}")

        if action == 'start':
            if self.scheduler:
                self.scheduler.force_on()
        elif action == 'stop':
            if self.scheduler:
                self.scheduler.force_off()
        elif action == 'resume':
            if self.scheduler:
                self.scheduler.clear_override()
        elif action == 'next':
            if self.display:
                self.display.skip_to_next()
        elif action == 'prev':
            if self.display:
                self.display.skip_to_previous()
        elif action == 'pause':
            if self.display:
                self.display.pause()
        elif action == 'toggle_pause':
            if self.display:
                self.display.toggle_pause()
        elif action == 'reload':
            self._on_config_change()

    def run(self) -> int:
        """
        Run the main application loop.

        Returns:
            Exit code (0 for success).
        """
        logger.info("Starting PhotoLoop...")

        # Load configuration
        if not self._load_config():
            return 1

        # Set up file logging if configured
        log_dir = os.environ.get('PHOTOLOOP_LOG_DIR', '/var/log/photoloop')
        try:
            setup_file_logging(log_dir)
        except Exception as e:
            logger.warning(f"Could not set up file logging: {e}")

        # Initialize components
        if not self._init_cache_manager():
            return 1

        if not self._init_scheduler():
            return 1

        if not self._init_display():
            return 1

        # Initialize remote control (optional, won't fail if no remote)
        self._init_remote()

        # Start background services
        self._start_web_server()
        self._start_sync_thread()

        # Do initial sync if no cached media
        if self.cache_manager.get_media_count()['total'] == 0:
            logger.info("No cached media, performing initial sync...")
            try:
                self.cache_manager.sync()
            except Exception as e:
                logger.error(f"Initial sync failed: {e}")

        self._running = True
        logger.info("PhotoLoop started successfully")

        # Main loop
        try:
            self._main_loop()
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            return 1
        finally:
            self._cleanup()

        return 0

    def _main_loop(self) -> None:
        """Main application loop."""
        from .display import DisplayMode

        last_state = None
        current_media = None

        while self._running and not self._shutdown_event.is_set():
            try:
                # Check scheduler state
                should_show = self.scheduler.should_show_slideshow()
                current_state = self.scheduler.get_current_state()

                # Log state changes
                if current_state != last_state:
                    logger.info(f"State changed: {current_state.value}")
                    last_state = current_state

                # Update display mode
                # Check if any albums are enabled for display
                has_albums = self.cache_manager.has_enabled_albums()

                if should_show and has_albums:
                    self.display.set_mode(DisplayMode.SLIDESHOW)

                    # Check if we need to load a new photo
                    # Skip/previous requests bypass duration and transition checks
                    go_next = self.display.is_skip_requested()
                    go_previous = self.display.is_previous_requested()
                    if (current_media is None or
                        go_next or
                        go_previous or
                        (self.display.is_photo_duration_complete() and
                         self.display.is_transition_complete())):

                        # Get next or previous media from cache
                        if go_previous:
                            next_media = self.cache_manager.get_previous_media()
                        else:
                            next_media = self.cache_manager.get_next_media()
                        if next_media:
                            # Compute display parameters
                            params = self.cache_manager.get_display_params(
                                next_media,
                                self.display.screen_width,
                                self.display.screen_height
                            )
                            # Show the photo
                            self.display.show_photo(
                                next_media,
                                params,
                                transition=(current_media is not None)
                            )
                            current_media = next_media
                            logger.info(f"Displaying photo: {os.path.basename(next_media.local_path)}")
                        elif current_media is None:
                            logger.warning("No media available to display")
                elif not has_albums:
                    # No albums enabled - stop slideshow (show black screen)
                    current_media = None
                    self.display.set_mode(DisplayMode.BLACK)
                else:
                    # Off-hours mode
                    current_media = None  # Reset so we load fresh when resuming
                    if self.config.schedule.off_hours_mode == 'clock':
                        self.display.set_mode(DisplayMode.CLOCK)
                    else:
                        self.display.set_mode(DisplayMode.BLACK)

                # Run display update (handles events, transitions, etc.)
                # Note: display.update() includes clock.tick() for frame rate control
                if not self.display.update():
                    # Display requested quit
                    logger.info("Display quit requested")
                    break

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(1)

    def stop(self) -> None:
        """Stop the application."""
        logger.info("Stopping PhotoLoop...")
        self._running = False
        self._shutdown_event.set()

    def _cleanup(self) -> None:
        """Clean up resources."""
        logger.info("Cleaning up...")

        if self.remote_handler:
            try:
                self.remote_handler.stop()
            except Exception as e:
                logger.error(f"Error cleaning up remote handler: {e}")

        if self.display:
            try:
                self.display.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up display: {e}")

        logger.info("PhotoLoop stopped")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="PhotoLoop - Raspberry Pi Photo Frame",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '-c', '--config',
        help='Path to configuration file'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    parser.add_argument(
        '--version',
        action='store_true',
        help='Show version and exit'
    )

    args = parser.parse_args()

    if args.version:
        from . import __version__
        print(f"PhotoLoop {__version__}")
        return 0

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    app = PhotoLoop(config_path=args.config)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
