# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
PhotoLoop Dashboard.
Provides configuration UI and REST API for control.
"""

import logging
import os
from functools import wraps
from typing import Any, Callable, Optional

from flask import Flask, jsonify, render_template, request, redirect, url_for

from ..config import PhotoLoopConfig, load_config, save_config, config_to_dict, validate_config

logger = logging.getLogger(__name__)


def create_app(
    config: PhotoLoopConfig,
    cache_manager: Any = None,
    scheduler: Any = None,
    display: Any = None,
    on_config_change: Optional[Callable] = None,
    on_sync_request: Optional[Callable] = None,
    on_control_request: Optional[Callable[[str], None]] = None
) -> Flask:
    """
    Create the Flask application.

    Args:
        config: PhotoLoop configuration.
        cache_manager: CacheManager instance (for status info).
        scheduler: Scheduler instance (for control).
        display: Display instance (for photo control status).
        on_config_change: Callback when config is saved.
        on_sync_request: Callback to trigger sync.
        on_control_request: Callback for control commands (start/stop/resume).

    Returns:
        Flask application.
    """
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
        static_folder=os.path.join(os.path.dirname(__file__), 'static')
    )

    # Store references
    app.photoloop_config = config
    app.cache_manager = cache_manager
    app.scheduler = scheduler
    app.display = display
    app.on_config_change = on_config_change
    app.on_sync_request = on_sync_request
    app.on_control_request = on_control_request

    # Routes

    @app.route('/')
    def index():
        """Main dashboard page."""
        return render_template(
            'settings.html',
            config=config_to_dict(app.photoloop_config)
        )

    @app.route('/sw.js')
    def service_worker():
        """Serve service worker from root for proper scope."""
        from flask import send_from_directory
        return send_from_directory(
            app.static_folder,
            'sw.js',
            mimetype='application/javascript'
        )

    @app.route('/api/status')
    def api_status():
        """Get current status."""
        status = {
            "running": True,
            "config_path": app.photoloop_config.config_path
        }

        # Add cache info
        if app.cache_manager:
            status["cache"] = {
                "counts": app.cache_manager.get_media_count(),
                "size_mb": round(app.cache_manager.get_cache_size_mb(), 1)
            }

        # Add schedule info
        if app.scheduler:
            status["schedule"] = app.scheduler.get_status()

        # Add schedule enabled from config
        status["schedule_enabled"] = app.photoloop_config.schedule.enabled

        # Add photo control status
        if app.display:
            status["photo_control"] = {
                "paused": app.display.is_paused() if hasattr(app.display, 'is_paused') else False
            }

        return jsonify(status)

    @app.route('/api/config', methods=['GET'])
    def api_get_config():
        """Get current configuration."""
        return jsonify(config_to_dict(app.photoloop_config))

    @app.route('/api/config', methods=['POST'])
    def api_set_config():
        """Update configuration."""
        try:
            data = request.get_json()
            if not data:
                return jsonify({"error": "No data provided"}), 400

            # Update config
            # This is a simplified implementation - a full one would
            # merge the data properly
            config_path = app.photoloop_config.config_path
            if config_path:
                import yaml
                with open(config_path, 'w') as f:
                    yaml.dump(data, f, default_flow_style=False)

                # Reload config
                new_config = load_config(config_path)
                app.photoloop_config = new_config

                # Validate
                errors = validate_config(new_config)
                if errors:
                    return jsonify({"warning": errors}), 200

                # Notify
                if app.on_config_change:
                    app.on_config_change()

                return jsonify({"success": True})
            else:
                return jsonify({"error": "No config path"}), 500

        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/albums', methods=['GET'])
    def api_get_albums():
        """Get configured albums and local directories with photo counts."""
        # Count photos per album and get sync times
        photo_counts = {}
        sync_times = {}
        if app.cache_manager:
            for media in app.cache_manager.get_all_media():
                src = media.album_source or ""
                photo_counts[src] = photo_counts.get(src, 0) + 1
            sync_times = app.cache_manager.get_album_sync_times()

        albums = []
        for a in app.photoloop_config.albums:
            album_name = a.name or (a.url if a.type == "google_photos" else a.path)
            albums.append({
                "type": a.type,
                "url": a.url,
                "path": a.path,
                "name": a.name,
                "enabled": a.enabled,
                "photo_count": photo_counts.get(album_name, 0),
                "last_synced": sync_times.get(album_name)
            })
        return jsonify(albums)

    @app.route('/api/albums', methods=['POST'])
    def api_add_album():
        """Add a new album or local directory."""
        try:
            data = request.get_json()
            album_type = data.get('type', 'google_photos').strip()
            name = data.get('name', '').strip()

            from ..config import AlbumConfig

            if album_type == "local":
                # Check if local albums are enabled
                if not app.photoloop_config.local_albums.enabled:
                    return jsonify({"error": "Local albums are disabled"}), 403

                # Local directory
                path = data.get('path', '').strip()
                if not path:
                    return jsonify({"error": "Path is required for local directories"}), 400

                # Expand and validate path
                expanded_path = os.path.expanduser(path)
                if not os.path.exists(expanded_path):
                    return jsonify({"error": f"Path does not exist: {path}"}), 400
                if not os.path.isdir(expanded_path):
                    return jsonify({"error": f"Path is not a directory: {path}"}), 400

                app.photoloop_config.albums.append(AlbumConfig(
                    path=path,
                    name=name,
                    type="local"
                ))
            else:
                # Google Photos album
                url = data.get('url', '').strip()
                if not url:
                    return jsonify({"error": "URL is required for Google Photos albums"}), 400

                app.photoloop_config.albums.append(AlbumConfig(
                    url=url,
                    name=name,
                    type="google_photos"
                ))

            save_config(app.photoloop_config)

            if app.on_config_change:
                app.on_config_change()

            return jsonify({"success": True})

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/api/albums/<int:index>', methods=['DELETE'])
    def api_delete_album(index: int):
        """Delete an album."""
        try:
            if 0 <= index < len(app.photoloop_config.albums):
                del app.photoloop_config.albums[index]
                save_config(app.photoloop_config)

                if app.on_config_change:
                    app.on_config_change()

                return jsonify({"success": True})
            else:
                return jsonify({"error": "Invalid index"}), 400

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/api/albums/<int:index>/enabled', methods=['POST'])
    def api_set_album_enabled(index: int):
        """Enable or disable an album for slideshow display."""
        try:
            data = request.get_json()
            enabled = data.get('enabled', True)

            if 0 <= index < len(app.photoloop_config.albums):
                app.photoloop_config.albums[index].enabled = enabled
                save_config(app.photoloop_config)

                # Notify main app to rebuild playlist
                if app.on_config_change:
                    app.on_config_change()

                return jsonify({"success": True, "enabled": enabled})
            else:
                return jsonify({"error": "Invalid album index"}), 400

        except Exception as e:
            logger.error(f"Error setting album enabled: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/albums/<int:index>/name', methods=['POST'])
    def api_set_album_name(index: int):
        """Update an album's display name."""
        try:
            data = request.get_json()
            name = data.get('name', '').strip()

            if 0 <= index < len(app.photoloop_config.albums):
                app.photoloop_config.albums[index].name = name
                save_config(app.photoloop_config)

                return jsonify({"success": True, "name": name})
            else:
                return jsonify({"error": "Invalid album index"}), 400

        except Exception as e:
            logger.error(f"Error setting album name: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/local-albums/config', methods=['GET'])
    def api_local_albums_config():
        """Get local albums configuration."""
        return jsonify({
            "enabled": app.photoloop_config.local_albums.enabled,
            "show_photo_counts": app.photoloop_config.local_albums.show_photo_counts
        })

    @app.route('/api/browse', methods=['POST'])
    def api_browse_directory():
        """Browse directories for local album selection."""
        # Check if local albums are enabled
        if not app.photoloop_config.local_albums.enabled:
            return jsonify({"error": "Local albums are disabled"}), 403

        try:
            data = request.get_json() or {}
            path = data.get('path', os.path.expanduser('~')).strip()

            # Expand path (handle ~ and relative paths)
            expanded = os.path.abspath(os.path.expanduser(path))

            # Security: verify path is under allowed browse_paths
            allowed = False
            for base in app.photoloop_config.local_albums.browse_paths:
                base_abs = os.path.abspath(os.path.expanduser(base))
                if expanded.startswith(base_abs) or expanded == base_abs:
                    allowed = True
                    break

            if not allowed:
                return jsonify({"error": "Access denied: path not in allowed browse paths"}), 403

            if not os.path.exists(expanded):
                return jsonify({"error": f"Path not found: {path}"}), 404

            if not os.path.isdir(expanded):
                return jsonify({"error": f"Not a directory: {path}"}), 400

            # List directories (skip hidden)
            items = []
            try:
                for name in sorted(os.listdir(expanded)):
                    if name.startswith('.'):
                        continue
                    full_path = os.path.join(expanded, name)
                    if os.path.isdir(full_path):
                        item = {"name": name, "path": full_path}
                        if app.photoloop_config.local_albums.show_photo_counts:
                            item["photo_count"] = _count_images(full_path)
                        items.append(item)
            except PermissionError:
                return jsonify({"error": "Permission denied"}), 403

            # Calculate parent path (but don't go above allowed roots)
            parent_path = os.path.dirname(expanded)
            parent_allowed = False
            for base in app.photoloop_config.local_albums.browse_paths:
                base_abs = os.path.abspath(os.path.expanduser(base))
                if parent_path.startswith(base_abs) or parent_path == base_abs:
                    parent_allowed = True
                    break

            return jsonify({
                "current_path": expanded,
                "parent_path": parent_path if parent_allowed else None,
                "items": items
            })

        except Exception as e:
            logger.error(f"Error browsing directory: {e}")
            return jsonify({"error": str(e)}), 500

    def _count_images(directory: str) -> int:
        """Count image files in a directory (non-recursive)."""
        IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.bmp', '.tiff'}
        count = 0
        try:
            for name in os.listdir(directory):
                ext = os.path.splitext(name.lower())[1]
                if ext in IMAGE_EXTENSIONS:
                    count += 1
        except (PermissionError, OSError):
            pass
        return count

    @app.route('/api/sync', methods=['POST'])
    def api_sync():
        """Trigger album sync."""
        try:
            if app.on_sync_request:
                # Get options from request body
                data = request.get_json() or {}
                update_all_captions = data.get('update_all_captions', False)
                force_refetch_captions = data.get('force_refetch_captions', False)

                app.on_sync_request(
                    update_all_captions=update_all_captions,
                    force_refetch_captions=force_refetch_captions
                )
                return jsonify({"success": True, "message": "Sync started"})
            else:
                return jsonify({"error": "Sync not available"}), 503

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/api/sync/status')
    def api_sync_status():
        """Get current sync progress."""
        if app.cache_manager:
            progress = app.cache_manager.get_sync_progress()
            return jsonify(progress.to_dict())
        else:
            return jsonify({
                "is_syncing": False,
                "stage": "idle",
                "error_message": "Cache manager not available"
            })

    @app.route('/api/extract-locations', methods=['POST'])
    def api_extract_locations():
        """Extract locations from GPS for photos without captions."""
        if not app.cache_manager:
            return jsonify({"error": "Cache manager not available"}), 503

        try:
            import threading

            def do_extract():
                updated = app.cache_manager.extract_locations()
                logger.info(f"Location extraction completed: {updated} photos updated")

            threading.Thread(target=do_extract, daemon=True).start()
            return jsonify({"success": True, "message": "Location extraction started"})

        except Exception as e:
            logger.error(f"Location extraction error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/control/<action>', methods=['POST'])
    def api_control(action: str):
        """Control the slideshow."""
        valid_actions = ['start', 'stop', 'resume', 'next', 'prev', 'pause', 'toggle_pause', 'reload', 'start_temp']

        if action not in valid_actions:
            return jsonify({"error": f"Invalid action. Valid: {valid_actions}"}), 400

        try:
            if app.on_control_request and action != 'start_temp':
                app.on_control_request(action)
                return jsonify({"success": True, "action": action})
            elif app.scheduler:
                # Direct scheduler control
                if action == 'start':
                    app.scheduler.force_on()
                elif action == 'stop':
                    app.scheduler.force_off()
                elif action == 'resume':
                    app.scheduler.clear_override()
                elif action == 'start_temp':
                    # Temporary override until next scheduled end time
                    until = app.scheduler.force_on_temporarily()
                    return jsonify({
                        "success": True,
                        "action": action,
                        "until": until.isoformat() if until else None
                    })
                return jsonify({"success": True, "action": action})
            else:
                return jsonify({"error": "Control not available"}), 503

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/api/schedule/enabled', methods=['POST'])
    def api_schedule_enabled():
        """Toggle schedule enabled state."""
        try:
            data = request.get_json()
            if data is None or 'enabled' not in data:
                return jsonify({"error": "Missing 'enabled' field"}), 400

            enabled = bool(data['enabled'])

            # Update config in memory
            app.photoloop_config.schedule.enabled = enabled

            # Save to config file
            config_path = app.photoloop_config.config_path
            if config_path:
                import yaml
                with open(config_path, 'r') as f:
                    config_data = yaml.safe_load(f) or {}

                if 'schedule' not in config_data:
                    config_data['schedule'] = {}
                config_data['schedule']['enabled'] = enabled

                with open(config_path, 'w') as f:
                    yaml.dump(config_data, f, default_flow_style=False)

            # Notify of config change
            if app.on_config_change:
                app.on_config_change()

            return jsonify({"success": True, "enabled": enabled})

        except Exception as e:
            logger.error(f"Error toggling schedule: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/schedule', methods=['POST'])
    def api_save_schedule():
        """Save schedule settings (times and off-hours mode)."""
        try:
            data = request.get_json()
            if data is None:
                return jsonify({"error": "No data provided"}), 400

            config_path = app.photoloop_config.config_path
            if config_path:
                import yaml
                with open(config_path, 'r') as f:
                    config_data = yaml.safe_load(f) or {}

                if 'schedule' not in config_data:
                    config_data['schedule'] = {}

                # Update weekday schedule
                if 'weekday_start' in data or 'weekday_end' in data:
                    if 'weekday' not in config_data['schedule']:
                        config_data['schedule']['weekday'] = {}
                    if 'weekday_start' in data:
                        config_data['schedule']['weekday']['start_time'] = data['weekday_start']
                    if 'weekday_end' in data:
                        config_data['schedule']['weekday']['end_time'] = data['weekday_end']

                # Update weekend schedule
                if 'weekend_start' in data or 'weekend_end' in data:
                    if 'weekend' not in config_data['schedule']:
                        config_data['schedule']['weekend'] = {}
                    if 'weekend_start' in data:
                        config_data['schedule']['weekend']['start_time'] = data['weekend_start']
                    if 'weekend_end' in data:
                        config_data['schedule']['weekend']['end_time'] = data['weekend_end']

                # Update off-hours mode
                if 'off_hours_mode' in data:
                    config_data['schedule']['off_hours_mode'] = data['off_hours_mode']

                with open(config_path, 'w') as f:
                    yaml.dump(config_data, f, default_flow_style=False)

            # Notify of config change
            if app.on_config_change:
                app.on_config_change()

            return jsonify({"success": True})

        except Exception as e:
            logger.error(f"Error saving schedule: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/display', methods=['POST'])
    def api_save_display():
        """Save display settings."""
        try:
            data = request.get_json()
            if data is None:
                return jsonify({"error": "No data provided"}), 400

            config_path = app.photoloop_config.config_path
            if config_path:
                import yaml
                with open(config_path, 'r') as f:
                    config_data = yaml.safe_load(f) or {}

                # Update display settings
                if 'display' not in config_data:
                    config_data['display'] = {}
                if 'photo_duration_seconds' in data:
                    config_data['display']['photo_duration_seconds'] = int(data['photo_duration_seconds'])
                if 'transition_type' in data:
                    config_data['display']['transition_type'] = data['transition_type']
                if 'order' in data:
                    config_data['display']['order'] = data['order']

                # Update ken_burns settings
                if 'ken_burns' not in config_data:
                    config_data['ken_burns'] = {}
                if 'ken_burns_enabled' in data:
                    config_data['ken_burns']['enabled'] = bool(data['ken_burns_enabled'])

                # Update overlay settings
                if 'overlay' not in config_data:
                    config_data['overlay'] = {}
                if 'overlay_enabled' in data:
                    config_data['overlay']['enabled'] = bool(data['overlay_enabled'])
                if 'overlay_font_size' in data:
                    config_data['overlay']['font_size'] = int(data['overlay_font_size'])

                with open(config_path, 'w') as f:
                    yaml.dump(config_data, f, default_flow_style=False)

            # Notify of config change
            if app.on_config_change:
                app.on_config_change()

            return jsonify({"success": True})

        except Exception as e:
            logger.error(f"Error saving display settings: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/photos')
    def api_photos():
        """Get list of cached photos, newest first."""
        if not app.cache_manager:
            return jsonify([])

        media = app.cache_manager.get_all_media()
        # Reverse order so most recently added photos appear first
        return jsonify([
            {
                "id": m.media_id,
                "type": m.media_type,
                "google_caption": m.google_caption,
                "embedded_caption": m.embedded_caption,
                "google_location": m.google_location,
                "date": m.exif_date,
                "path": m.local_path
            }
            for m in reversed(media)
        ])

    @app.route('/api/photos/<media_id>/thumbnail')
    def api_photo_thumbnail(media_id: str):
        """Get photo thumbnail (200px, cached)."""
        if not app.cache_manager:
            return "Not found", 404

        for m in app.cache_manager.get_all_media():
            if m.media_id == media_id:
                if not os.path.exists(m.local_path):
                    continue

                # Check for cached thumbnail
                thumb_dir = os.path.join(app.cache_manager.cache_dir, 'thumbnails')
                thumb_path = os.path.join(thumb_dir, f"{media_id}_thumb.jpg")

                if not os.path.exists(thumb_path):
                    # Generate thumbnail
                    try:
                        os.makedirs(thumb_dir, exist_ok=True)
                        from PIL import Image
                        with Image.open(m.local_path) as img:
                            img.thumbnail((200, 200))
                            img.save(thumb_path, "JPEG", quality=70)
                    except Exception as e:
                        logger.error(f"Error generating thumbnail: {e}")
                        # Fall back to original
                        from flask import send_file
                        return send_file(m.local_path, mimetype='image/jpeg')

                from flask import send_file
                response = send_file(thumb_path, mimetype='image/jpeg')
                response.headers['Cache-Control'] = 'public, max-age=86400'  # Cache 1 day
                return response

        return "Not found", 404

    @app.route('/api/cache/clear', methods=['POST'])
    def api_clear_cache():
        """Clear all cached photos."""
        try:
            if app.cache_manager:
                app.cache_manager.clear_cache()
                return jsonify({"success": True, "message": "Cache cleared"})
            else:
                return jsonify({"error": "Cache manager not available"}), 503
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            return jsonify({"error": str(e)}), 500

    return app


def run_web_server(
    config: PhotoLoopConfig,
    cache_manager: Any = None,
    scheduler: Any = None,
    on_config_change: Optional[Callable] = None,
    on_sync_request: Optional[Callable] = None,
    on_control_request: Optional[Callable[[str], None]] = None
) -> None:
    """
    Run the web server (blocking).

    Args:
        config: PhotoLoop configuration.
        cache_manager: CacheManager instance.
        scheduler: Scheduler instance.
        on_config_change: Config change callback.
        on_sync_request: Sync request callback.
        on_control_request: Control request callback.
    """
    if not config.web.enabled:
        logger.info("Web interface disabled")
        return

    app = create_app(
        config,
        cache_manager,
        scheduler,
        on_config_change,
        on_sync_request,
        on_control_request
    )

    logger.info(f"Starting web server on {config.web.host}:{config.web.port}")

    # Disable Flask's default logging for production
    import logging as stdlib_logging
    stdlib_logging.getLogger('werkzeug').setLevel(stdlib_logging.WARNING)

    app.run(
        host=config.web.host,
        port=config.web.port,
        debug=False,
        threaded=True
    )
