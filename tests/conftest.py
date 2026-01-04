# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""
Pytest configuration and shared fixtures for PhotoLoop tests.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config_dict():
    """Return a minimal valid config dictionary."""
    return {
        "albums": [
            {"url": "https://photos.app.goo.gl/test123", "name": "Test Album"}
        ],
        "sync": {
            "interval_minutes": 60,
            "full_resolution": True,
            "max_dimension": 1920
        },
        "display": {
            "resolution": "auto",
            "photo_duration_seconds": 30,
            "video_enabled": False,
            "transition_type": "fade",
            "transition_duration_ms": 1000,
            "order": "random"
        },
        "scaling": {
            "mode": "fill",
            "smart_crop": True,
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
        },
        "schedule": {
            "enabled": False,
            "off_hours_mode": "black",
            "weekday": {"start_time": "07:00", "end_time": "22:00"},
            "weekend": {"start_time": "08:00", "end_time": "23:00"}
        },
        "cache": {
            "directory": "/tmp/photoloop_test_cache",
            "max_size_mb": 100
        },
        "web": {
            "enabled": False,
            "port": 8080,
            "host": "127.0.0.1"
        }
    }


@pytest.fixture
def sample_config_yaml(temp_dir, sample_config_dict):
    """Create a temporary config.yaml file."""
    import yaml
    config_path = temp_dir / "config.yaml"
    with open(config_path, 'w') as f:
        yaml.dump(sample_config_dict, f)
    return config_path


@pytest.fixture
def sample_metadata():
    """Return sample cache metadata."""
    return {
        "media": {
            "abc123": {
                "media_id": "abc123",
                "url": "https://lh3.googleusercontent.com/test1",
                "local_path": "/tmp/cache/abc123.jpg",
                "media_type": "photo",
                "caption": "Test photo 1",
                "exif_date": "2024-01-15T10:30:00",
                "album_source": "Test Album",
                "download_date": "2024-12-01T12:00:00",
                "last_seen": "2024-12-24T10:00:00",
                "content_hash": "deadbeef",
                "cached_faces": None,
                "display_params": None,
                "deleted": False
            },
            "def456": {
                "media_id": "def456",
                "url": "https://lh3.googleusercontent.com/test2",
                "local_path": "/tmp/cache/def456.jpg",
                "media_type": "photo",
                "caption": None,
                "exif_date": None,
                "album_source": "Test Album",
                "download_date": "2024-12-01T12:00:00",
                "last_seen": "2024-12-24T10:00:00",
                "content_hash": "cafebabe",
                "cached_faces": [
                    {"x": 0.3, "y": 0.2, "width": 0.1, "height": 0.15, "confidence": 0.95}
                ],
                "display_params": None,
                "deleted": False
            }
        },
        "last_updated": "2024-12-24T10:00:00",
        "settings": {
            "max_dimension": 1920,
            "full_resolution": True,
            "scaling": {
                "mode": "fill",
                "max_crop_percent": 15,
                "face_position": "center",
                "fallback_crop": "center"
            },
            "face_detection": {
                "enabled": True,
                "confidence_threshold": 0.6,
                "model_version": "yunet_2023mar"
            }
        }
    }


@pytest.fixture
def sample_faces():
    """Return sample face detection results."""
    from src.face_detector import FaceRegion
    return [
        FaceRegion(x=0.3, y=0.2, width=0.1, height=0.15, confidence=0.95),
        FaceRegion(x=0.6, y=0.25, width=0.08, height=0.12, confidence=0.87),
    ]
