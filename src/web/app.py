"""
Web interface for PhotoLoop.
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
        """Get configured albums."""
        albums = [
            {"url": a.url, "name": a.name}
            for a in app.photoloop_config.albums
        ]
        return jsonify(albums)

    @app.route('/api/albums', methods=['POST'])
    def api_add_album():
        """Add a new album."""
        try:
            data = request.get_json()
            url = data.get('url', '').strip()
            name = data.get('name', '').strip()

            if not url:
                return jsonify({"error": "URL is required"}), 400

            # Add album to config
            from ..config import AlbumConfig
            app.photoloop_config.albums.append(AlbumConfig(url=url, name=name))
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

    @app.route('/api/sync', methods=['POST'])
    def api_sync():
        """Trigger album sync."""
        try:
            if app.on_sync_request:
                # Get options from request body
                data = request.get_json() or {}
                update_all_captions = data.get('update_all_captions', False)

                app.on_sync_request(update_all_captions=update_all_captions)
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

    @app.route('/api/control/<action>', methods=['POST'])
    def api_control(action: str):
        """Control the slideshow."""
        valid_actions = ['start', 'stop', 'resume', 'next', 'reload']

        if action not in valid_actions:
            return jsonify({"error": f"Invalid action. Valid: {valid_actions}"}), 400

        try:
            if app.on_control_request:
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
                return jsonify({"success": True, "action": action})
            else:
                return jsonify({"error": "Control not available"}), 503

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/api/photos')
    def api_photos():
        """Get list of cached photos."""
        if not app.cache_manager:
            return jsonify([])

        media = app.cache_manager.get_all_media()
        return jsonify([
            {
                "id": m.media_id,
                "type": m.media_type,
                "caption": m.caption,
                "date": m.exif_date,
                "path": m.local_path
            }
            for m in media  # Return all photos
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
