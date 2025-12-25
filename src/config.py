"""
Configuration management for PhotoLoop.
Handles loading, validation, and defaults for all settings.
"""

import os
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_CONFIG_PATHS = [
    "/etc/photoloop/config.yaml",
    os.path.expanduser("~/.config/photoloop/config.yaml"),
    "./config.yaml",
]


@dataclass
class AlbumConfig:
    """Configuration for a single album."""
    url: str
    name: str = ""


@dataclass
class SyncConfig:
    """Sync settings."""
    interval_minutes: int = 60
    full_resolution: bool = True
    max_dimension: int = 1920


@dataclass
class DisplayConfig:
    """Display settings."""
    resolution: str = "auto"
    photo_duration_seconds: int = 30
    video_enabled: bool = True
    transition_type: str = "fade"  # fade, slide_left, slide_right, slide_up, slide_down, random
    transition_duration_ms: int = 1000
    order: str = "random"  # random, sequential


@dataclass
class ScalingConfig:
    """Scaling and cropping settings."""
    mode: str = "fill"  # fill, fit, balanced, stretch
    smart_crop: bool = True
    face_detection: bool = True
    face_position: str = "center"  # center, rule_of_thirds, top_third
    fallback_crop: str = "center"  # center, top, bottom
    # For "balanced" mode: max percentage of image that can be cropped (0-50)
    max_crop_percent: int = 15
    # Background/fill color for letterbox/pillarbox bars [R, G, B]
    background_color: List[int] = field(default_factory=lambda: [0, 0, 0])


@dataclass
class KenBurnsConfig:
    """Ken Burns effect settings."""
    enabled: bool = True
    zoom_range: List[float] = field(default_factory=lambda: [1.0, 1.15])
    pan_speed: float = 0.02
    randomize: bool = True


@dataclass
class OverlayConfig:
    """Overlay settings for date and caption display."""
    enabled: bool = True
    show_date: bool = True
    show_caption: bool = True
    date_format: str = "%B %d, %Y"
    position: str = "bottom_left"  # bottom_left, bottom_right, top_left, top_right
    font_size: int = 24
    font_color: List[int] = field(default_factory=lambda: [255, 255, 255])
    background_color: List[int] = field(default_factory=lambda: [0, 0, 0, 128])
    padding: int = 20
    max_caption_length: int = 200


@dataclass
class ScheduleTimeConfig:
    """Schedule time for a period."""
    start_time: str = "07:00"
    end_time: str = "22:00"


@dataclass
class ScheduleConfig:
    """Schedule settings."""
    enabled: bool = True
    off_hours_mode: str = "black"  # black, clock
    weekday: ScheduleTimeConfig = field(default_factory=ScheduleTimeConfig)
    weekend: ScheduleTimeConfig = field(default_factory=lambda: ScheduleTimeConfig(
        start_time="08:00", end_time="23:00"
    ))
    overrides: Dict[str, ScheduleTimeConfig] = field(default_factory=dict)


@dataclass
class CacheConfig:
    """Cache settings."""
    directory: str = "/var/lib/photoloop/cache"
    max_size_mb: int = 1000


@dataclass
class WebConfig:
    """Web interface settings."""
    enabled: bool = True
    port: int = 8080
    host: str = "0.0.0.0"


@dataclass
class PhotoLoopConfig:
    """Main configuration class."""
    albums: List[AlbumConfig] = field(default_factory=list)
    sync: SyncConfig = field(default_factory=SyncConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    scaling: ScalingConfig = field(default_factory=ScalingConfig)
    ken_burns: KenBurnsConfig = field(default_factory=KenBurnsConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    web: WebConfig = field(default_factory=WebConfig)

    # Runtime state (not persisted)
    config_path: Optional[str] = None


def _dict_to_dataclass(data: Dict[str, Any], cls: type) -> Any:
    """Convert a dictionary to a dataclass instance, handling nested dataclasses."""
    if data is None:
        return cls()

    field_types = {f.name: f.type for f in cls.__dataclass_fields__.values()}
    kwargs = {}

    for key, value in data.items():
        if key not in field_types:
            continue

        field_type = field_types[key]

        # Handle nested dataclasses
        if hasattr(field_type, '__dataclass_fields__'):
            kwargs[key] = _dict_to_dataclass(value, field_type)
        # Handle Dict[str, ScheduleTimeConfig] for overrides
        elif key == 'overrides' and isinstance(value, dict):
            kwargs[key] = {
                k: _dict_to_dataclass(v, ScheduleTimeConfig)
                for k, v in value.items()
            }
        else:
            kwargs[key] = value

    return cls(**kwargs)


def load_config(config_path: Optional[str] = None) -> PhotoLoopConfig:
    """
    Load configuration from a YAML file.

    Args:
        config_path: Path to config file. If None, searches default locations.

    Returns:
        PhotoLoopConfig instance with loaded or default values.
    """
    # Find config file
    if config_path:
        paths_to_try = [config_path]
    else:
        paths_to_try = DEFAULT_CONFIG_PATHS

    config_data = {}
    found_path = None

    for path in paths_to_try:
        expanded_path = os.path.expanduser(path)
        if os.path.exists(expanded_path):
            try:
                with open(expanded_path, 'r') as f:
                    config_data = yaml.safe_load(f) or {}
                found_path = expanded_path
                logger.info(f"Loaded config from {expanded_path}")
                break
            except Exception as e:
                logger.warning(f"Failed to load config from {expanded_path}: {e}")

    if not found_path:
        logger.info("No config file found, using defaults")

    # Parse albums
    albums = []
    for album_data in config_data.get('albums', []):
        if isinstance(album_data, str):
            albums.append(AlbumConfig(url=album_data))
        elif isinstance(album_data, dict):
            albums.append(AlbumConfig(
                url=album_data.get('url', ''),
                name=album_data.get('name', '')
            ))

    # Build config object
    config = PhotoLoopConfig(
        albums=albums,
        sync=_dict_to_dataclass(config_data.get('sync'), SyncConfig),
        display=_dict_to_dataclass(config_data.get('display'), DisplayConfig),
        scaling=_dict_to_dataclass(config_data.get('scaling'), ScalingConfig),
        ken_burns=_dict_to_dataclass(config_data.get('ken_burns'), KenBurnsConfig),
        overlay=_dict_to_dataclass(config_data.get('overlay'), OverlayConfig),
        schedule=_dict_to_dataclass(config_data.get('schedule'), ScheduleConfig),
        cache=_dict_to_dataclass(config_data.get('cache'), CacheConfig),
        web=_dict_to_dataclass(config_data.get('web'), WebConfig),
        config_path=found_path,
    )

    # Expand cache directory path
    config.cache.directory = os.path.expanduser(config.cache.directory)

    return config


def save_config(config: PhotoLoopConfig, config_path: Optional[str] = None) -> str:
    """
    Save configuration to a YAML file.

    Args:
        config: Configuration to save.
        config_path: Path to save to. If None, uses config.config_path or default.

    Returns:
        Path where config was saved.
    """
    if config_path is None:
        config_path = config.config_path or DEFAULT_CONFIG_PATHS[0]

    config_path = os.path.expanduser(config_path)

    # Ensure directory exists
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    # Convert to dict for YAML serialization
    data = config_to_dict(config)

    with open(config_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Saved config to {config_path}")
    return config_path


def config_to_dict(config: PhotoLoopConfig) -> Dict[str, Any]:
    """Convert config to dictionary for serialization."""
    def dataclass_to_dict(obj: Any) -> Any:
        if hasattr(obj, '__dataclass_fields__'):
            result = {}
            for field_name in obj.__dataclass_fields__:
                if field_name == 'config_path':
                    continue  # Skip runtime state
                value = getattr(obj, field_name)
                result[field_name] = dataclass_to_dict(value)
            return result
        elif isinstance(obj, list):
            return [dataclass_to_dict(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: dataclass_to_dict(v) for k, v in obj.items()}
        else:
            return obj

    return dataclass_to_dict(config)


def validate_config(config: PhotoLoopConfig) -> List[str]:
    """
    Validate configuration and return list of errors.

    Returns:
        List of error messages. Empty if valid.
    """
    errors = []

    # Check albums
    if not config.albums:
        errors.append("No albums configured. Add at least one album URL.")

    for i, album in enumerate(config.albums):
        if not album.url:
            errors.append(f"Album {i+1} has no URL.")
        elif not (album.url.startswith('http://') or album.url.startswith('https://')):
            errors.append(f"Album {i+1} URL must start with http:// or https://")

    # Check display settings
    valid_transitions = ['fade', 'slide_left', 'slide_right', 'slide_up', 'slide_down', 'random']
    if config.display.transition_type not in valid_transitions:
        errors.append(f"Invalid transition type. Must be one of: {valid_transitions}")

    if config.display.order not in ['random', 'sequential']:
        errors.append("Display order must be 'random' or 'sequential'")

    # Check scaling settings
    if config.scaling.mode not in ['fill', 'fit', 'balanced', 'stretch']:
        errors.append("Scaling mode must be 'fill', 'fit', 'balanced', or 'stretch'")

    if not (0 <= config.scaling.max_crop_percent <= 50):
        errors.append("max_crop_percent must be between 0 and 50")

    if config.scaling.face_position not in ['center', 'rule_of_thirds', 'top_third']:
        errors.append("Face position must be 'center', 'rule_of_thirds', or 'top_third'")

    # Check Ken Burns settings
    if len(config.ken_burns.zoom_range) != 2:
        errors.append("Ken Burns zoom_range must have exactly 2 values [min, max]")
    elif config.ken_burns.zoom_range[0] > config.ken_burns.zoom_range[1]:
        errors.append("Ken Burns zoom_range min must be <= max")

    # Check overlay settings
    valid_positions = ['bottom_left', 'bottom_right', 'top_left', 'top_right']
    if config.overlay.position not in valid_positions:
        errors.append(f"Overlay position must be one of: {valid_positions}")

    # Check schedule settings
    if config.schedule.off_hours_mode not in ['black', 'clock']:
        errors.append("Off hours mode must be 'black' or 'clock'")

    # Check cache settings
    if config.cache.max_size_mb < 100:
        errors.append("Cache max_size_mb should be at least 100 MB")

    # Check web settings
    if config.web.port < 1 or config.web.port > 65535:
        errors.append("Web port must be between 1 and 65535")

    return errors
