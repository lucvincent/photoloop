# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Tests for configuration loading and validation.
"""

import pytest
import yaml
from pathlib import Path


class TestConfigValidation:
    """Test config validation logic."""

    def test_valid_config_passes(self, sample_config_yaml):
        """Valid config should load without errors."""
        from src.config import load_config, validate_config

        config = load_config(str(sample_config_yaml))
        errors = validate_config(config)

        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_invalid_scaling_mode(self, temp_dir, sample_config_dict):
        """Invalid scaling mode should produce error."""
        from src.config import load_config, validate_config

        sample_config_dict["scaling"]["mode"] = "invalid_mode"
        config_path = temp_dir / "config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(sample_config_dict, f)

        config = load_config(str(config_path))
        errors = validate_config(config)

        assert any("Scaling mode" in e for e in errors)

    def test_invalid_max_crop_percent(self, temp_dir, sample_config_dict):
        """max_crop_percent outside 0-50 should produce error."""
        from src.config import load_config, validate_config

        sample_config_dict["scaling"]["max_crop_percent"] = 75
        config_path = temp_dir / "config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(sample_config_dict, f)

        config = load_config(str(config_path))
        errors = validate_config(config)

        assert any("max_crop_percent" in e for e in errors)

    def test_valid_scaling_modes(self, temp_dir, sample_config_dict):
        """All valid scaling modes should pass validation."""
        from src.config import load_config, validate_config

        for mode in ["fill", "fit", "balanced", "stretch"]:
            sample_config_dict["scaling"]["mode"] = mode
            config_path = temp_dir / "config.yaml"
            with open(config_path, 'w') as f:
                yaml.dump(sample_config_dict, f)

            config = load_config(str(config_path))
            errors = validate_config(config)

            scaling_errors = [e for e in errors if "Scaling mode" in e]
            assert len(scaling_errors) == 0, f"Mode '{mode}' should be valid"

    def test_missing_album_url(self, temp_dir, sample_config_dict):
        """Missing album URL should produce error."""
        from src.config import load_config, validate_config

        sample_config_dict["albums"] = [{"name": "No URL Album"}]
        config_path = temp_dir / "config.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(sample_config_dict, f)

        config = load_config(str(config_path))
        errors = validate_config(config)

        assert any("album" in e.lower() or "url" in e.lower() for e in errors)


class TestConfigDefaults:
    """Test that config defaults are applied correctly."""

    def test_scaling_defaults(self):
        """Scaling config should have correct defaults."""
        from src.config import ScalingConfig

        config = ScalingConfig()

        assert config.mode == "fill"
        assert config.max_crop_percent == 15
        assert config.background_color == [0, 0, 0]
        assert config.face_detection == True

    def test_ken_burns_defaults(self):
        """Ken Burns config should have correct defaults."""
        from src.config import KenBurnsConfig

        config = KenBurnsConfig()

        assert config.enabled == True
        assert config.zoom_range == [1.0, 1.15]
        assert config.pan_speed == 0.02
