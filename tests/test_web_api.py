# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Tests for PhotoLoop Dashboard web API endpoints.
"""

import json
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch


class TestWebAppFixtures:
    """Fixtures for web API tests."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock PhotoLoop config."""
        config = MagicMock()
        config.config_path = "/etc/photoloop/config.yaml"
        config.cache.directory = "/var/lib/photoloop/cache"
        config.cache.max_size_mb = 1000
        config.web.enabled = True
        config.web.host = "0.0.0.0"
        config.web.port = 8080
        config.schedule.enabled = True
        config.schedule.off_hours_mode = "black"
        config.schedule.weekday.start_time = "07:00"
        config.schedule.weekday.end_time = "22:00"
        config.schedule.weekend.start_time = "08:00"
        config.schedule.weekend.end_time = "23:00"
        # Create album mocks with explicit property values
        album1 = MagicMock()
        album1.url = "https://photos.app.goo.gl/test1"
        album1.name = "Album 1"
        album1.enabled = True

        album2 = MagicMock()
        album2.url = "https://photos.app.goo.gl/test2"
        album2.name = "Album 2"
        album2.enabled = False

        config.albums = [album1, album2]
        return config

    @pytest.fixture
    def mock_cache_manager(self):
        """Create a mock cache manager."""
        cache_manager = MagicMock()
        cache_manager.get_media_count.return_value = {"photos": 100, "videos": 5, "total": 105}
        cache_manager.get_cache_size_mb.return_value = 250.5
        cache_manager.get_all_media.return_value = [
            MagicMock(
                media_id="photo1",
                media_type="photo",
                caption="Test caption 1",
                exif_date="2024-06-15",
                local_path="/var/lib/photoloop/cache/photo1.jpg",
                album_source="Album 1"
            ),
            MagicMock(
                media_id="photo2",
                media_type="photo",
                caption=None,
                exif_date="2024-07-20",
                local_path="/var/lib/photoloop/cache/photo2.jpg",
                album_source="Album 1"
            ),
        ]
        cache_manager.get_sync_progress.return_value = MagicMock(
            to_dict=lambda: {
                "is_syncing": False,
                "stage": "idle",
                "album_name": "",
                "albums_done": 0,
                "albums_total": 0,
                "urls_found": 0,
                "downloads_done": 0,
                "downloads_total": 0,
                "error_message": "",
                "started_at": None,
                "completed_at": None
            }
        )
        return cache_manager

    @pytest.fixture
    def mock_scheduler(self):
        """Create a mock scheduler."""
        scheduler = MagicMock()
        scheduler.get_status.return_value = {
            "state": "active",
            "has_override": False,
            "has_temporary_override": False,
            "temporary_override_until": None,
            "next_transition": None,
            "schedule_enabled": True
        }
        scheduler.force_on_temporarily.return_value = datetime(2025, 1, 6, 22, 0)
        return scheduler

    @pytest.fixture
    def mock_display(self):
        """Create a mock display."""
        display = MagicMock()
        display.is_paused.return_value = False
        return display

    @pytest.fixture
    def app(self, mock_config, mock_cache_manager, mock_scheduler, mock_display):
        """Create Flask test app."""
        from src.web.app import create_app

        # Track callback calls
        callbacks = {
            "config_changed": False,
            "sync_requested": False,
            "sync_update_all_captions": False,
            "control_action": None
        }

        def on_config_change():
            callbacks["config_changed"] = True

        def on_sync_request(update_all_captions=False):
            callbacks["sync_requested"] = True
            callbacks["sync_update_all_captions"] = update_all_captions

        def on_control_request(action):
            callbacks["control_action"] = action

        app = create_app(
            mock_config,
            cache_manager=mock_cache_manager,
            scheduler=mock_scheduler,
            display=mock_display,
            on_config_change=on_config_change,
            on_sync_request=on_sync_request,
            on_control_request=on_control_request
        )
        app.config["TESTING"] = True
        app.callbacks = callbacks  # Store for test assertions
        return app

    @pytest.fixture
    def client(self, app):
        """Create Flask test client."""
        return app.test_client()


class TestStatusEndpoint(TestWebAppFixtures):
    """Tests for GET /api/status endpoint."""

    def test_status_returns_running(self, client):
        """Test that status endpoint returns running state."""
        response = client.get("/api/status")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["running"] is True

    def test_status_includes_config_path(self, client):
        """Test that status includes config path."""
        response = client.get("/api/status")
        data = json.loads(response.data)
        assert "config_path" in data
        assert data["config_path"] == "/etc/photoloop/config.yaml"

    def test_status_includes_cache_info(self, client):
        """Test that status includes cache information."""
        response = client.get("/api/status")
        data = json.loads(response.data)
        assert "cache" in data
        assert data["cache"]["counts"]["photos"] == 100
        assert data["cache"]["counts"]["videos"] == 5
        assert data["cache"]["size_mb"] == 250.5

    def test_status_includes_schedule_info(self, client):
        """Test that status includes schedule information."""
        response = client.get("/api/status")
        data = json.loads(response.data)
        assert "schedule" in data
        assert data["schedule"]["state"] == "active"
        assert data["schedule_enabled"] is True

    def test_status_includes_photo_control(self, client):
        """Test that status includes photo control state."""
        response = client.get("/api/status")
        data = json.loads(response.data)
        assert "photo_control" in data
        assert data["photo_control"]["paused"] is False


class TestConfigEndpoints(TestWebAppFixtures):
    """Tests for /api/config endpoints."""

    def test_get_config(self, client):
        """Test GET /api/config returns config."""
        with patch("src.web.app.config_to_dict") as mock_to_dict:
            mock_to_dict.return_value = {"test": "config"}
            response = client.get("/api/config")
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data == {"test": "config"}

    def test_post_config_no_data(self, client):
        """Test POST /api/config with no data returns error."""
        response = client.post(
            "/api/config",
            data=json.dumps(None),
            content_type="application/json"
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "error" in data


class TestAlbumEndpoints(TestWebAppFixtures):
    """Tests for /api/albums endpoints."""

    def test_get_albums(self, client, mock_config):
        """Test GET /api/albums returns album list."""
        response = client.get("/api/albums")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 2
        assert data[0]["name"] == "Album 1"
        assert data[0]["enabled"] is True
        assert data[1]["name"] == "Album 2"
        assert data[1]["enabled"] is False

    def test_add_album_success(self, client, app, mock_config):
        """Test POST /api/albums adds new album."""
        with patch("src.web.app.save_config"):
            response = client.post(
                "/api/albums",
                data=json.dumps({"url": "https://photos.app.goo.gl/new", "name": "New Album"}),
                content_type="application/json"
            )
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["success"] is True
            assert app.callbacks["config_changed"] is True

    def test_add_album_no_url(self, client):
        """Test POST /api/albums without URL returns error."""
        response = client.post(
            "/api/albums",
            data=json.dumps({"name": "No URL Album"}),
            content_type="application/json"
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "error" in data

    def test_delete_album_success(self, client, app, mock_config):
        """Test DELETE /api/albums/<index> removes album."""
        with patch("src.web.app.save_config"):
            response = client.delete("/api/albums/0")
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["success"] is True
            assert app.callbacks["config_changed"] is True

    def test_delete_album_invalid_index(self, client):
        """Test DELETE /api/albums with invalid index returns error."""
        response = client.delete("/api/albums/99")
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "error" in data

    def test_set_album_enabled(self, client, app, mock_config):
        """Test POST /api/albums/<index>/enabled toggles enabled state."""
        with patch("src.web.app.save_config"):
            response = client.post(
                "/api/albums/1/enabled",
                data=json.dumps({"enabled": True}),
                content_type="application/json"
            )
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["success"] is True
            assert data["enabled"] is True

    def test_set_album_name(self, client, mock_config):
        """Test POST /api/albums/<index>/name updates album name."""
        with patch("src.web.app.save_config"):
            response = client.post(
                "/api/albums/0/name",
                data=json.dumps({"name": "Renamed Album"}),
                content_type="application/json"
            )
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["success"] is True
            assert data["name"] == "Renamed Album"


class TestSyncEndpoints(TestWebAppFixtures):
    """Tests for /api/sync endpoints."""

    def test_trigger_sync(self, client, app):
        """Test POST /api/sync triggers sync."""
        response = client.post(
            "/api/sync",
            data=json.dumps({}),
            content_type="application/json"
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert app.callbacks["sync_requested"] is True
        assert app.callbacks["sync_update_all_captions"] is False

    def test_trigger_sync_with_caption_update(self, client, app):
        """Test POST /api/sync with update_all_captions flag."""
        response = client.post(
            "/api/sync",
            data=json.dumps({"update_all_captions": True}),
            content_type="application/json"
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert app.callbacks["sync_update_all_captions"] is True

    def test_get_sync_status(self, client):
        """Test GET /api/sync/status returns sync progress."""
        response = client.get("/api/sync/status")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["is_syncing"] is False
        assert data["stage"] == "idle"


class TestControlEndpoints(TestWebAppFixtures):
    """Tests for /api/control/<action> endpoint."""

    def test_control_start(self, client, app):
        """Test POST /api/control/start."""
        response = client.post("/api/control/start")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["action"] == "start"
        assert app.callbacks["control_action"] == "start"

    def test_control_stop(self, client, app):
        """Test POST /api/control/stop."""
        response = client.post("/api/control/stop")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["action"] == "stop"
        assert app.callbacks["control_action"] == "stop"

    def test_control_resume(self, client, app):
        """Test POST /api/control/resume."""
        response = client.post("/api/control/resume")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["action"] == "resume"

    def test_control_next(self, client, app):
        """Test POST /api/control/next."""
        response = client.post("/api/control/next")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["action"] == "next"
        assert app.callbacks["control_action"] == "next"

    def test_control_prev(self, client, app):
        """Test POST /api/control/prev."""
        response = client.post("/api/control/prev")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["action"] == "prev"
        assert app.callbacks["control_action"] == "prev"

    def test_control_pause(self, client, app):
        """Test POST /api/control/pause."""
        response = client.post("/api/control/pause")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["action"] == "pause"

    def test_control_toggle_pause(self, client, app):
        """Test POST /api/control/toggle_pause."""
        response = client.post("/api/control/toggle_pause")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["action"] == "toggle_pause"

    def test_control_reload(self, client, app):
        """Test POST /api/control/reload."""
        response = client.post("/api/control/reload")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["action"] == "reload"

    def test_control_start_temp(self, client, mock_scheduler):
        """Test POST /api/control/start_temp for temporary override."""
        response = client.post("/api/control/start_temp")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["action"] == "start_temp"
        assert data["until"] == "2025-01-06T22:00:00"
        mock_scheduler.force_on_temporarily.assert_called_once()

    def test_control_invalid_action(self, client):
        """Test POST /api/control with invalid action."""
        response = client.post("/api/control/invalid_action")
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "error" in data


class TestScheduleEndpoints(TestWebAppFixtures):
    """Tests for /api/schedule/enabled endpoint."""

    def test_enable_schedule(self, client, app, mock_config, tmp_path):
        """Test POST /api/schedule/enabled enables schedule."""
        # Create temp config file
        config_file = tmp_path / "config.yaml"
        config_file.write_text("schedule:\n  enabled: false\n")
        mock_config.config_path = str(config_file)

        response = client.post(
            "/api/schedule/enabled",
            data=json.dumps({"enabled": True}),
            content_type="application/json"
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        assert data["enabled"] is True

    def test_disable_schedule(self, client, app, mock_config, tmp_path):
        """Test POST /api/schedule/enabled disables schedule."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("schedule:\n  enabled: true\n")
        mock_config.config_path = str(config_file)

        response = client.post(
            "/api/schedule/enabled",
            data=json.dumps({"enabled": False}),
            content_type="application/json"
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["enabled"] is False

    def test_schedule_enabled_missing_field(self, client):
        """Test POST /api/schedule/enabled without enabled field."""
        response = client.post(
            "/api/schedule/enabled",
            data=json.dumps({}),
            content_type="application/json"
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "error" in data


class TestPhotosEndpoints(TestWebAppFixtures):
    """Tests for /api/photos endpoints."""

    def test_get_photos(self, client):
        """Test GET /api/photos returns photo list."""
        response = client.get("/api/photos")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 2
        assert data[0]["id"] == "photo1"
        assert data[0]["caption"] == "Test caption 1"
        assert data[1]["id"] == "photo2"
        assert data[1]["caption"] is None

    def test_get_photos_no_cache_manager(self, mock_config, mock_scheduler, mock_display):
        """Test GET /api/photos with no cache manager returns empty list."""
        from src.web.app import create_app
        app = create_app(mock_config, cache_manager=None, scheduler=mock_scheduler, display=mock_display)
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.get("/api/photos")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == []

    def test_get_thumbnail_not_found(self, client):
        """Test GET /api/photos/<id>/thumbnail with invalid ID."""
        response = client.get("/api/photos/nonexistent/thumbnail")
        assert response.status_code == 404


class TestCacheEndpoints(TestWebAppFixtures):
    """Tests for /api/cache endpoints."""

    def test_clear_cache(self, client, mock_cache_manager):
        """Test POST /api/cache/clear clears the cache."""
        response = client.post("/api/cache/clear")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True
        mock_cache_manager.clear_cache.assert_called_once()

    def test_clear_cache_no_manager(self, mock_config, mock_scheduler, mock_display):
        """Test POST /api/cache/clear with no cache manager."""
        from src.web.app import create_app
        app = create_app(mock_config, cache_manager=None, scheduler=mock_scheduler, display=mock_display)
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.post("/api/cache/clear")
        assert response.status_code == 503
        data = json.loads(response.data)
        assert "error" in data


class TestExtractLocationsEndpoint(TestWebAppFixtures):
    """Tests for /api/extract-locations endpoint."""

    def test_extract_locations(self, client, mock_cache_manager):
        """Test POST /api/extract-locations starts extraction."""
        response = client.post("/api/extract-locations")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True

    def test_extract_locations_no_cache_manager(self, mock_config, mock_scheduler, mock_display):
        """Test POST /api/extract-locations with no cache manager."""
        from src.web.app import create_app
        app = create_app(mock_config, cache_manager=None, scheduler=mock_scheduler, display=mock_display)
        app.config["TESTING"] = True
        client = app.test_client()

        response = client.post("/api/extract-locations")
        assert response.status_code == 503


class TestIndexPage(TestWebAppFixtures):
    """Tests for the main dashboard page."""

    def test_index_returns_html(self, client):
        """Test GET / returns HTML page."""
        with patch("src.web.app.config_to_dict") as mock_to_dict:
            # Return a complete config dict that matches template expectations
            mock_to_dict.return_value = {
                "schedule": {
                    "enabled": True,
                    "off_hours_mode": "black",
                    "weekday": {"start_time": "07:00", "end_time": "22:00"},
                    "weekend": {"start_time": "08:00", "end_time": "23:00"}
                },
                "display": {
                    "photo_duration_seconds": 30,
                    "transition_type": "fade",
                    "transition_duration_ms": 1000,
                    "order": "random",
                    "video_enabled": False,
                    "resolution": "auto"
                },
                "sync": {"interval_minutes": 60, "max_dimension": 1920, "full_resolution": False},
                "albums": [],
                "cache": {"directory": "/tmp/cache", "max_size_mb": 1000},
                "web": {"port": 8080, "host": "0.0.0.0", "enabled": True},
                "scaling": {
                    "mode": "fill",
                    "face_detection": True,
                    "face_position": "center",
                    "fallback_crop": "center",
                    "max_crop_percent": 15,
                    "background_color": [0, 0, 0]
                },
                "ken_burns": {
                    "enabled": True,
                    "zoom_range": [1.0, 1.15],
                    "pan_speed": 0.02,
                    "randomize": True
                },
                "overlay": {
                    "enabled": True,
                    "show_date": True,
                    "show_caption": True,
                    "date_format": "%B %d, %Y",
                    "position": "bottom_left",
                    "font_size": 24,
                    "font_color": [255, 255, 255],
                    "background_color": [0, 0, 0, 128],
                    "padding": 20,
                    "max_caption_length": 200
                }
            }
            response = client.get("/")
            assert response.status_code == 200
            assert response.content_type == "text/html; charset=utf-8"
