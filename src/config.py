# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
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

# ruamel.yaml preserves comments when loading/saving
try:
    from ruamel.yaml import YAML
    RUAMEL_AVAILABLE = True
except ImportError:
    RUAMEL_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_CONFIG_PATHS = [
    "/etc/photoloop/config.yaml",
    os.path.expanduser("~/.config/photoloop/config.yaml"),
    "./config.yaml",
]


@dataclass
class AlbumConfig:
    """Configuration for a single photo source (album or local directory).

    Supported types:
        - "google_photos": Public Google Photos album (requires url)
        - "local": Local directory on filesystem (requires path)
    """
    url: str = ""                         # Google Photos URL (for type=google_photos)
    path: str = ""                        # Local directory path (for type=local)
    name: str = ""                        # Display name
    enabled: bool = True                  # Whether to include in slideshow
    type: str = "google_photos"           # "google_photos" or "local"


@dataclass
class SyncConfig:
    """Sync settings."""
    interval_minutes: int = 1440  # Default: 24 hours
    sync_on_start: bool = False  # Whether to sync immediately on service start
    sync_time: Optional[str] = None  # Time of day for first sync (HH:MM format, e.g., "03:00")
    full_resolution: bool = True
    max_dimension: int = 1920
    # Note: Caption priority is now configured in overlay.caption_sources at display time


@dataclass
class DisplayConfig:
    """Display settings."""
    resolution: str = "auto"
    photo_duration_seconds: int = 30
    video_enabled: bool = True
    transition_type: str = "fade"  # fade, slide_left, slide_right, slide_up, slide_down, random
    transition_duration_ms: int = 1000
    order: str = "random"  # random, recency_weighted, alphabetical, chronological
    # Recency-weighted random settings (only used when order = "recency_weighted")
    recency_cutoff_years: float = 5.0  # Photos older than this all have equal weight
    recency_min_weight: float = 0.33   # Weight at cutoff age (1.0 = no bias, lower = stronger bias)
    # Display power control method for off-hours/stop
    #   - auto: try wlopm (DPMS), then HDMI-CEC, then black screen (default)
    #   - wlopm: Wayland DPMS only (for monitors on labwc/Wayland)
    #   - cec: HDMI-CEC only (for TVs)
    #   - none: just show black screen, don't control display power
    power_control: str = "auto"


@dataclass
class ScalingConfig:
    """Scaling and cropping settings."""
    mode: str = "fill"  # fill, fit, balanced, stretch
    smart_crop: bool = True
    # Smart crop method: which algorithm to use for intelligent cropping
    #   - face: Use YuNet face detection (default, fastest)
    #   - saliency: Use U2-Net to detect all visually important regions
    #   - aesthetic: Use GAIC model to find aesthetically pleasing crops
    smart_crop_method: str = "face"  # face, saliency, aesthetic
    face_detection: bool = True  # Deprecated: use smart_crop_method instead
    face_position: str = "center"  # center, rule_of_thirds, top_third
    fallback_crop: str = "center"  # center, top, bottom
    # For "balanced" mode: max percentage of image that can be cropped (0-50)
    max_crop_percent: int = 15
    # For saliency method: minimum saliency threshold (0-1)
    saliency_threshold: float = 0.3
    # For saliency method: how much of total saliency to include in crop (0-1)
    saliency_coverage: float = 0.9
    # Crop bias: prioritize preserving a certain part of the image
    #   - none: Let smart crop decide based on detected content
    #   - top: Minimize cropping from top (preserve sky/mountains)
    #   - bottom: Minimize cropping from bottom
    crop_bias: str = "none"  # none, top, bottom
    # Background/fill color for letterbox/pillarbox bars [R, G, B]
    background_color: List[int] = field(default_factory=lambda: [0, 0, 0])


@dataclass
class KenBurnsConfig:
    """Ken Burns effect settings (experimental - disabled by default)."""
    enabled: bool = False
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
    # Date source preference - which date to display when multiple are available
    # Options:
    #   "exif_first"   - Use EXIF date if available, fall back to Google Photos date (default)
    #   "google_first" - Use Google Photos date if available, fall back to EXIF date
    #   "exif_only"    - Only show EXIF date, never Google Photos date
    #   "google_only"  - Only show Google Photos date, never EXIF date
    # Note: EXIF date comes from photo metadata (embedded in file)
    #       Google date comes from Google Photos album (scraped from web UI)
    date_source: str = "exif_first"
    # Caption source priorities (lower number = higher priority)
    # Available sources: google_caption, embedded_caption, google_location, exif_location
    caption_sources: Dict[str, int] = field(default_factory=lambda: {
        "google_caption": 1,
        "embedded_caption": 2,
        "google_location": 3,
        "exif_location": 4
    })
    # How many caption sources to show (1 = just highest priority with data)
    max_caption_sources: int = 1
    # Separator between multiple caption sources
    caption_separator: str = " — "


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
class RenderedCacheConfig:
    """Settings for pre-rendered display frame cache.

    Caches photos after cropping at the native display resolution for instant loading.
    """
    # Whether rendered frame caching is enabled
    enabled: bool = True

    # Maximum disk space for rendered frames (MB)
    # 0 = no limit (let cache grow freely)
    # Rendered frames are regenerable, so this cache can be cleared if disk is tight
    max_size_mb: int = 0

    # JPEG quality for cached frames (90-98)
    # Higher = better quality but larger files
    # 95 is a good balance (~2-4 MB per 4K frame)
    quality: int = 95


@dataclass
class WebConfig:
    """Web interface settings."""
    enabled: bool = True
    port: int = 8080
    host: str = "0.0.0.0"


@dataclass
class LocalAlbumsConfig:
    """Local album browser settings."""
    enabled: bool = True  # Allow adding local directories
    browse_paths: List[str] = field(default_factory=lambda: ["/home", "/media", "/mnt"])
    show_photo_counts: bool = True  # Show image count in browser (slower)


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
    local_albums: LocalAlbumsConfig = field(default_factory=LocalAlbumsConfig)
    rendered_cache: RenderedCacheConfig = field(default_factory=RenderedCacheConfig)

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

    # Parse albums (supports both Google Photos albums and local directories)
    albums = []
    for album_data in config_data.get('albums', []):
        if isinstance(album_data, str):
            # Legacy format: just a URL string
            albums.append(AlbumConfig(url=album_data, type="google_photos"))
        elif isinstance(album_data, dict):
            album_type = album_data.get('type', 'google_photos')
            albums.append(AlbumConfig(
                url=album_data.get('url', ''),
                path=album_data.get('path', ''),
                name=album_data.get('name', ''),
                enabled=album_data.get('enabled', True),
                type=album_type
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
        local_albums=_dict_to_dataclass(config_data.get('local_albums'), LocalAlbumsConfig),
        rendered_cache=_dict_to_dataclass(config_data.get('rendered_cache'), RenderedCacheConfig),
        config_path=found_path,
    )

    # Expand cache directory path
    config.cache.directory = os.path.expanduser(config.cache.directory)

    return config


def save_config(config: PhotoLoopConfig, config_path: Optional[str] = None) -> str:
    """
    Save configuration to a YAML file.

    Uses ruamel.yaml when available to preserve comments in the config file.
    Falls back to pyyaml (which strips comments) if ruamel.yaml is not installed.

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
    new_data = config_to_dict(config)

    if RUAMEL_AVAILABLE and os.path.exists(config_path):
        # Use ruamel.yaml to preserve comments
        _save_config_with_comments(config_path, new_data)
    else:
        # Fallback to pyyaml (strips comments)
        with open(config_path, 'w') as f:
            yaml.dump(new_data, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Saved config to {config_path}")
    return config_path


def _save_config_with_comments(config_path: str, new_data: Dict[str, Any]) -> None:
    """
    Save config while preserving comments from the existing file.

    Loads the existing config with ruamel.yaml (which preserves comments),
    updates values from new_data, and writes back.
    """
    ruamel = YAML()
    ruamel.preserve_quotes = True
    ruamel.width = 100

    # Load existing config with comments
    with open(config_path, 'r') as f:
        existing = ruamel.load(f)

    if existing is None:
        existing = {}

    # Recursively update existing with new values
    _update_recursive(existing, new_data)

    # Write back with comments preserved
    with open(config_path, 'w') as f:
        ruamel.dump(existing, f)


def _update_recursive(target: Dict, source: Dict) -> Dict:
    """Update target dict with source values, preserving target's structure."""
    if not isinstance(target, dict) or not isinstance(source, dict):
        return source

    for key, value in source.items():
        if key in target:
            if isinstance(target[key], dict) and isinstance(value, dict):
                _update_recursive(target[key], value)
            else:
                target[key] = value
        else:
            target[key] = value

    # Remove keys that are in target but not in source
    keys_to_remove = [k for k in target if k not in source]
    for key in keys_to_remove:
        del target[key]

    return target


def save_config_partial(config_path: str, updates: Dict[str, Any]) -> None:
    """
    Update specific keys in config file while preserving comments.

    Unlike save_config() which replaces the entire config, this function
    only updates the specified keys, leaving other keys and all comments intact.

    Args:
        config_path: Path to the config file.
        updates: Dictionary of updates to apply (can be nested).

    Example:
        save_config_partial('/etc/photoloop/config.yaml', {
            'schedule': {'enabled': True},
            'display': {'photo_duration_seconds': 10}
        })
    """
    config_path = os.path.expanduser(config_path)

    if RUAMEL_AVAILABLE and os.path.exists(config_path):
        ruamel = YAML()
        ruamel.preserve_quotes = True
        ruamel.width = 100

        with open(config_path, 'r') as f:
            existing = ruamel.load(f)

        if existing is None:
            existing = {}

        # Apply updates (without removing keys not in updates)
        def apply_updates(target, source):
            for key, value in source.items():
                if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                    apply_updates(target[key], value)
                else:
                    target[key] = value

        apply_updates(existing, updates)

        with open(config_path, 'w') as f:
            ruamel.dump(existing, f)
    else:
        # Fallback: load with pyyaml, update, save (loses comments)
        with open(config_path, 'r') as f:
            existing = yaml.safe_load(f) or {}

        def apply_updates(target, source):
            for key, value in source.items():
                if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                    apply_updates(target[key], value)
                else:
                    target[key] = value

        apply_updates(existing, updates)

        with open(config_path, 'w') as f:
            yaml.dump(existing, f, default_flow_style=False, sort_keys=False)


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

    # Check albums (supports both Google Photos albums and local directories)
    if not config.albums:
        errors.append("No albums configured. Add at least one album URL or local directory.")

    for i, album in enumerate(config.albums):
        if album.type not in ('google_photos', 'local'):
            errors.append(f"Album {i+1} has invalid type '{album.type}'. Must be 'google_photos' or 'local'.")
        elif album.type == 'google_photos':
            # Validate Google Photos album
            if not album.url:
                errors.append(f"Album {i+1} (Google Photos) has no URL.")
            elif not (album.url.startswith('http://') or album.url.startswith('https://')):
                errors.append(f"Album {i+1} URL must start with http:// or https://")
        elif album.type == 'local':
            # Validate local directory
            if not album.path:
                errors.append(f"Album {i+1} (local) has no path.")
            else:
                expanded_path = os.path.expanduser(album.path)
                if not os.path.exists(expanded_path):
                    errors.append(f"Album {i+1} path does not exist: {album.path}")
                elif not os.path.isdir(expanded_path):
                    errors.append(f"Album {i+1} path is not a directory: {album.path}")

    # Check display settings
    valid_transitions = ['fade', 'slide_left', 'slide_right', 'slide_up', 'slide_down', 'random']
    if config.display.transition_type not in valid_transitions:
        errors.append(f"Invalid transition type. Must be one of: {valid_transitions}")

    valid_orders = ['random', 'recency_weighted', 'alphabetical', 'chronological']
    if config.display.order not in valid_orders:
        errors.append(f"Display order must be one of: {valid_orders}")

    # Check scaling settings
    if config.scaling.mode not in ['fill', 'fit', 'balanced', 'stretch']:
        errors.append("Scaling mode must be 'fill', 'fit', 'balanced', or 'stretch'")

    if config.scaling.smart_crop_method not in ['face', 'saliency', 'aesthetic']:
        errors.append("Smart crop method must be 'face', 'saliency', or 'aesthetic'")

    if not (0 <= config.scaling.max_crop_percent <= 50):
        errors.append("max_crop_percent must be between 0 and 50")

    if not (0 <= config.scaling.saliency_threshold <= 1):
        errors.append("saliency_threshold must be between 0 and 1")

    if not (0 <= config.scaling.saliency_coverage <= 1):
        errors.append("saliency_coverage must be between 0 and 1")

    if config.scaling.face_position not in ['center', 'rule_of_thirds', 'top_third']:
        errors.append("Face position must be 'center', 'rule_of_thirds', or 'top_third'")

    if config.scaling.crop_bias not in ['none', 'top', 'bottom']:
        errors.append("Crop bias must be 'none', 'top', or 'bottom'")

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

    # Check rendered cache settings
    if config.rendered_cache.max_size_mb < 0:
        errors.append("Rendered cache max_size_mb must be 0 (no limit) or positive")

    if not (70 <= config.rendered_cache.quality <= 100):
        errors.append("Rendered cache quality must be between 70 and 100")

    return errors
