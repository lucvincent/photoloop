# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Cache manager for PhotoLoop.
Handles downloading, storing, and managing photos/videos from Google Photos.
"""

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
import random

import requests
from PIL import Image

from .album_scraper import AlbumScraper, MediaItem
from .config import PhotoLoopConfig
from .face_detector import FaceDetector, FaceRegion, faces_from_dict, faces_to_dict
from .image_processor import DisplayParams, ImageProcessor
from .metadata import MetadataExtractor, init_geocode_cache, reverse_geocode

logger = logging.getLogger(__name__)


@dataclass
class SyncProgress:
    """Tracks the current sync progress for UI display."""
    is_syncing: bool = False
    stage: str = ""  # "idle", "scraping", "downloading", "complete", "error"
    album_name: str = ""
    albums_done: int = 0
    albums_total: int = 0
    urls_found: int = 0
    downloads_done: int = 0
    downloads_total: int = 0
    error_message: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "is_syncing": self.is_syncing,
            "stage": self.stage,
            "album_name": self.album_name,
            "albums_done": self.albums_done,
            "albums_total": self.albums_total,
            "urls_found": self.urls_found,
            "downloads_done": self.downloads_done,
            "downloads_total": self.downloads_total,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class CachedMedia:
    """Metadata for a cached media item."""
    media_id: str                    # Hash of original URL or file path
    url: str                         # Original URL (Google Photos) or "file://" + path (local)
    local_path: str                  # Path to cached/local file
    media_type: str                  # "photo" or "video"
    google_caption: Optional[str] = None    # Caption/description from Google Photos DOM
    embedded_caption: Optional[str] = None  # Caption from embedded EXIF/IPTC metadata
    exif_date: Optional[str] = None  # ISO format date string from EXIF metadata
    google_date: Optional[str] = None  # ISO format date string from Google Photos DOM
    album_source: str = ""           # Which album/directory it came from
    download_date: str = ""          # When first downloaded/indexed
    last_seen: str = ""              # Last time seen in album scrape/directory scan
    content_hash: str = ""           # Hash of file content
    cached_faces: Optional[List[Dict[str, Any]]] = None  # Detected faces (separate from display_params)
    display_params: Optional[Dict[str, Any]] = None  # Cached display parameters
    deleted: bool = False            # Marked for deletion
    location: Optional[str] = None   # Reverse-geocoded location from EXIF GPS
    gps_latitude: Optional[float] = None   # EXIF GPS latitude for lazy geocoding
    gps_longitude: Optional[float] = None  # EXIF GPS longitude for lazy geocoding
    google_location: Optional[str] = None  # Location scraped from Google Photos info panel
    google_metadata_fetched: bool = False  # True once Google DOM metadata has been fetched (even if empty)
    source_type: str = "google_photos"  # "google_photos" or "local"
    file_mtime: Optional[str] = None    # For local files: ISO timestamp of file mtime for change detection

    def to_dict(self) -> dict:
        return {
            "media_id": self.media_id,
            "url": self.url,
            "local_path": self.local_path,
            "media_type": self.media_type,
            "google_caption": self.google_caption,
            "embedded_caption": self.embedded_caption,
            "exif_date": self.exif_date,
            "google_date": self.google_date,
            "album_source": self.album_source,
            "download_date": self.download_date,
            "last_seen": self.last_seen,
            "content_hash": self.content_hash,
            "cached_faces": self.cached_faces,
            "display_params": self.display_params,
            "deleted": self.deleted,
            "location": self.location,
            "gps_latitude": self.gps_latitude,
            "gps_longitude": self.gps_longitude,
            "google_location": self.google_location,
            "google_metadata_fetched": self.google_metadata_fetched,
            "source_type": self.source_type,
            "file_mtime": self.file_mtime
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CachedMedia":
        # Migration: handle old 'caption' field
        google_caption = data.get("google_caption")
        embedded_caption = data.get("embedded_caption")

        # If old 'caption' field exists and new fields don't, migrate it
        if "caption" in data and google_caption is None and embedded_caption is None:
            old_caption = data.get("caption")
            if old_caption:
                # If Google metadata was fetched, old caption is likely Google caption
                # Otherwise it's the embedded caption
                if data.get("google_metadata_fetched", False):
                    google_caption = old_caption
                else:
                    embedded_caption = old_caption

        return cls(
            media_id=data["media_id"],
            url=data["url"],
            local_path=data["local_path"],
            media_type=data["media_type"],
            google_caption=google_caption,
            embedded_caption=embedded_caption,
            exif_date=data.get("exif_date"),
            google_date=data.get("google_date"),
            album_source=data.get("album_source", ""),
            download_date=data.get("download_date", ""),
            last_seen=data.get("last_seen", ""),
            content_hash=data.get("content_hash", ""),
            cached_faces=data.get("cached_faces"),
            display_params=data.get("display_params"),
            deleted=data.get("deleted", False),
            location=data.get("location"),
            gps_latitude=data.get("gps_latitude"),
            gps_longitude=data.get("gps_longitude"),
            google_location=data.get("google_location"),
            google_metadata_fetched=data.get("google_metadata_fetched", False),
            source_type=data.get("source_type", "google_photos"),  # Default for backward compat
            file_mtime=data.get("file_mtime")
        )


class CacheManager:
    """
    Manages the local cache of photos and videos.

    Handles:
    - Downloading media from Google Photos albums
    - Indexing media from local directories
    - Storing metadata in JSON database
    - Incremental sync (only download/re-index new/changed items)
    - Cache size management
    - Providing media for display
    """

    METADATA_FILE = "metadata.json"

    def __init__(self, config: PhotoLoopConfig):
        """
        Initialize the cache manager.

        Args:
            config: PhotoLoop configuration.
        """
        self.config = config
        self.cache_dir = Path(config.cache.directory)
        self.metadata_path = self.cache_dir / self.METADATA_FILE

        # Thread safety
        self._lock = threading.RLock()
        self._sync_lock = threading.Lock()  # Prevent concurrent syncs

        # In-memory cache of metadata
        self._media: Dict[str, CachedMedia] = {}

        # Per-album sync timestamps (album_name -> ISO datetime)
        self._album_sync_times: Dict[str, str] = {}

        # Sync progress tracking
        self._sync_progress = SyncProgress()

        # Components
        self._scraper = AlbumScraper(headless=True, timeout=120)
        self._scraper.set_progress_callback(self._on_scraper_progress)
        self._face_detector: Optional[FaceDetector] = None
        self._metadata_extractor = MetadataExtractor()
        self._image_processor: Optional[ImageProcessor] = None

        # Playback state
        self._playlist: List[str] = []
        self._playlist_index: int = 0

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Initialize geocoding cache
        init_geocode_cache(str(self.cache_dir))

        # Load existing metadata
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load metadata from disk."""
        with self._lock:
            if self.metadata_path.exists():
                try:
                    with open(self.metadata_path, 'r') as f:
                        data = json.load(f)

                    # Check if resolution settings changed
                    cached_settings = data.get("settings", {})
                    current_max_dim = self.config.sync.max_dimension
                    current_full_res = self.config.sync.full_resolution
                    cached_max_dim = cached_settings.get("max_dimension")
                    cached_full_res = cached_settings.get("full_resolution")

                    if (cached_max_dim is not None and
                        (cached_max_dim != current_max_dim or cached_full_res != current_full_res)):
                        logger.warning(
                            f"Resolution settings changed (was: {cached_max_dim}px/full={cached_full_res}, "
                            f"now: {current_max_dim}px/full={current_full_res}). "
                            f"Clearing cache to re-download at new resolution."
                        )
                        # Delete all cached files
                        for media_id, media_data in data.get("media", {}).items():
                            try:
                                local_path = media_data.get("local_path")
                                if local_path and os.path.exists(local_path):
                                    os.remove(local_path)
                            except Exception:
                                pass
                        # Clear metadata
                        self._media = {}
                        self._playlist = []
                        self._save_metadata()
                        logger.info("Cache cleared due to resolution change")
                        return

                    # Check if scaling settings changed (only invalidates display_params, not files or faces)
                    current_scaling = {
                        "mode": self.config.scaling.mode,
                        "max_crop_percent": self.config.scaling.max_crop_percent,
                        "face_position": self.config.scaling.face_position,
                        "fallback_crop": self.config.scaling.fallback_crop,
                    }
                    cached_scaling = cached_settings.get("scaling", {})
                    scaling_changed = (
                        cached_scaling and
                        any(cached_scaling.get(k) != v for k, v in current_scaling.items())
                    )

                    # Check if face detection settings changed (invalidates cached_faces)
                    current_face_detection = {
                        "enabled": self.config.scaling.face_detection,
                        "confidence_threshold": 0.6,  # Default from FaceDetector
                        "model_version": "yunet_2023mar",
                    }
                    cached_face_detection = cached_settings.get("face_detection", {})
                    face_detection_changed = (
                        cached_face_detection and
                        any(cached_face_detection.get(k) != v for k, v in current_face_detection.items())
                    )

                    self._media = {
                        k: CachedMedia.from_dict(v)
                        for k, v in data.get("media", {}).items()
                    }

                    # Load per-album sync timestamps
                    self._album_sync_times = data.get("album_sync_times", {})

                    # If face detection settings changed, clear cached_faces to force re-detection
                    if face_detection_changed:
                        logger.info(
                            f"Face detection settings changed, invalidating cached faces "
                            f"for {len(self._media)} items"
                        )
                        for cached in self._media.values():
                            cached.cached_faces = None
                            cached.display_params = None  # Also invalidate display params since faces affect crop
                        self._save_metadata()
                    # If only scaling settings changed, clear display_params but keep cached faces
                    elif scaling_changed:
                        logger.info(
                            f"Scaling settings changed, invalidating display parameters "
                            f"for {len(self._media)} items (keeping cached faces)"
                        )
                        for cached in self._media.values():
                            cached.display_params = None
                        self._save_metadata()

                    logger.info(f"Loaded {len(self._media)} cached items from metadata")
                except Exception as e:
                    logger.error(f"Failed to load metadata: {e}")
                    self._media = {}

    def _save_metadata(self) -> None:
        """Save metadata to disk atomically.

        Writes to a temp file first, then renames to prevent corruption
        if the write is interrupted.
        """
        with self._lock:
            try:
                data = {
                    "media": {k: v.to_dict() for k, v in self._media.items()},
                    "album_sync_times": self._album_sync_times,
                    "last_updated": datetime.now().isoformat(),
                    "settings": {
                        "max_dimension": self.config.sync.max_dimension,
                        "full_resolution": self.config.sync.full_resolution,
                        "scaling": {
                            "mode": self.config.scaling.mode,
                            "max_crop_percent": self.config.scaling.max_crop_percent,
                            "face_position": self.config.scaling.face_position,
                            "fallback_crop": self.config.scaling.fallback_crop,
                        },
                        "face_detection": {
                            "enabled": self.config.scaling.face_detection,
                            "confidence_threshold": 0.6,  # Default from FaceDetector
                            "model_version": "yunet_2023mar",  # Track model version
                        }
                    }
                }
                # Write to temp file first, then rename atomically
                temp_path = str(self.metadata_path) + '.tmp'
                with open(temp_path, 'w') as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())  # Ensure data is written to disk
                os.replace(temp_path, self.metadata_path)  # Atomic rename
            except Exception as e:
                logger.error(f"Failed to save metadata: {e}")

    def _on_scraper_progress(self, stage: str, current: int, total: int) -> None:
        """Callback from album scraper to update progress."""
        self._sync_progress.urls_found = current
        if stage == "loading":
            self._sync_progress.stage = "scraping"
        elif stage == "scrolling":
            self._sync_progress.stage = "scraping"
        elif stage == "complete":
            self._sync_progress.stage = "scraping"

    def get_sync_progress(self) -> SyncProgress:
        """Get current sync progress for UI display."""
        return self._sync_progress

    def _get_media_id(self, url: str) -> str:
        """Generate a stable ID for a media URL."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def _get_content_hash(self, file_path: str) -> str:
        """Calculate hash of file content."""
        hasher = hashlib.md5()
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    def _download_media(
        self,
        url: str,
        media_type: str,
        media_id: str
    ) -> Optional[str]:
        """
        Download a media file from Google Photos.

        Args:
            url: Base URL of the media.
            media_type: "photo" or "video".
            media_id: ID for the filename.

        Returns:
            Local file path or None on failure.
        """
        # Construct download URL
        if media_type == "video":
            download_url = self._scraper.get_video_download_url(url)
            extension = ".mp4"
        else:
            if self.config.sync.full_resolution:
                download_url = self._scraper.get_full_resolution_url(url)
            else:
                download_url = self._scraper.get_sized_url(
                    url,
                    self.config.sync.max_dimension,
                    self.config.sync.max_dimension
                )
            extension = ".jpg"

        local_path = self.cache_dir / f"{media_id}{extension}"

        try:
            logger.debug(f"Downloading {media_type}: {download_url}")

            response = requests.get(
                download_url,
                stream=True,
                timeout=60,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
                }
            )
            response.raise_for_status()

            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"Downloaded: {local_path.name}")
            return str(local_path)

        except Exception as e:
            logger.error(f"Failed to download {url}: {e}")
            if local_path.exists():
                local_path.unlink()
            return None

    # Supported file extensions for local directory scanning
    PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif'}
    VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}

    def _scan_local_directory(self, path: str, album_name: str) -> List[MediaItem]:
        """
        Scan a local directory recursively for photos and videos.

        Args:
            path: Path to the local directory.
            album_name: Name to assign to items from this directory.

        Returns:
            List of MediaItem objects for found media files.
        """
        items: List[MediaItem] = []
        expanded_path = os.path.expanduser(path)

        if not os.path.isdir(expanded_path):
            logger.warning(f"Local directory does not exist: {path}")
            return items

        # Track visited inodes to avoid infinite loops from symlinks
        visited_inodes: Set[int] = set()

        all_extensions = self.PHOTO_EXTENSIONS | self.VIDEO_EXTENSIONS

        def scan_dir(dir_path: str) -> None:
            """Recursively scan directory."""
            try:
                # Check for symlink loops
                try:
                    stat_info = os.stat(dir_path)
                    if stat_info.st_ino in visited_inodes:
                        logger.debug(f"Skipping already-visited directory (symlink loop): {dir_path}")
                        return
                    visited_inodes.add(stat_info.st_ino)
                except OSError:
                    return

                entries = os.listdir(dir_path)
            except PermissionError:
                logger.warning(f"Permission denied accessing directory: {dir_path}")
                return
            except OSError as e:
                logger.warning(f"Error accessing directory {dir_path}: {e}")
                return

            for entry in entries:
                # Skip hidden files and directories
                if entry.startswith('.'):
                    continue

                full_path = os.path.join(dir_path, entry)

                try:
                    if os.path.isdir(full_path):
                        # Recurse into subdirectory
                        scan_dir(full_path)
                    elif os.path.isfile(full_path):
                        # Check extension (case-insensitive)
                        ext = os.path.splitext(entry)[1].lower()
                        if ext in all_extensions:
                            # Determine media type
                            if ext in self.PHOTO_EXTENSIONS:
                                media_type = "photo"
                            else:
                                media_type = "video"

                            # Create MediaItem with file:// URL for unique identification
                            abs_path = os.path.abspath(full_path)
                            item = MediaItem(
                                url=f"file://{abs_path}",
                                media_type=media_type,
                                caption=None,
                                thumbnail_url=None,
                                album_name=album_name
                            )
                            items.append(item)

                except PermissionError:
                    logger.warning(f"Permission denied accessing file: {full_path}")
                except OSError as e:
                    logger.warning(f"Error accessing {full_path}: {e}")

        logger.info(f"Scanning local directory: {expanded_path}")
        scan_dir(expanded_path)
        logger.info(f"Found {len(items)} media files in {path}")

        return items

    def sync(
        self,
        force_full: bool = False,
        update_all_captions: bool = False,
        force_refetch_captions: bool = False
    ) -> Dict[str, int]:
        """
        Sync cache with Google Photos albums.

        Args:
            force_full: Force re-download of all items.
            update_all_captions: If True, fetch captions for photos missing metadata.
            force_refetch_captions: If True, re-fetch captions for ALL photos
                (even those with existing data). Use after changing extraction logic.

        Returns:
            Statistics dict with counts of new/updated/deleted items.
        """
        stats = {"new": 0, "updated": 0, "deleted": 0, "unchanged": 0, "errors": 0}

        # Prevent concurrent syncs
        if not self._sync_lock.acquire(blocking=False):
            logger.warning("Sync already in progress, skipping")
            return stats

        try:
            return self._do_sync(force_full, update_all_captions, force_refetch_captions)
        finally:
            self._sync_lock.release()

    def _do_sync(
        self,
        force_full: bool = False,
        update_all_captions: bool = False,
        force_refetch_captions: bool = False
    ) -> Dict[str, int]:
        """Internal sync implementation."""
        stats = {"new": 0, "updated": 0, "deleted": 0, "unchanged": 0, "errors": 0, "captions_updated": 0}

        logger.info("Starting album sync...")

        # Initialize sync progress
        self._sync_progress = SyncProgress(
            is_syncing=True,
            stage="scraping",
            started_at=datetime.now().isoformat()
        )

        # Get current time for last_seen
        now = datetime.now().isoformat()

        # Scrape all configured albums and scan local directories
        all_items: List[MediaItem] = []
        # Track source type for each item (keyed by URL)
        item_source_types: Dict[str, str] = {}
        albums_scraped_successfully = 0
        # Count enabled albums that need processing (Google Photos with URL or local with path)
        total_albums = sum(
            1 for a in self.config.albums
            if a.enabled and ((a.type == "google_photos" and a.url) or (a.type == "local" and a.path))
        )
        self._sync_progress.albums_total = total_albums

        for album in self.config.albums:
            # Skip disabled albums
            if not album.enabled:
                continue

            try:
                if album.type == "google_photos" and album.url:
                    # Google Photos album - scrape via Selenium
                    album_name = album.name or album.url
                    logger.info(f"Scraping Google Photos album: {album_name}")
                    self._sync_progress.album_name = album_name
                    self._sync_progress.urls_found = 0

                    items = self._scraper.scrape_album(album.url)
                    for item in items:
                        item.album_name = album_name
                        item_source_types[item.url] = "google_photos"
                    all_items.extend(items)
                    albums_scraped_successfully += 1
                    self._sync_progress.albums_done = albums_scraped_successfully
                    # Record sync timestamp for this album
                    self._album_sync_times[album_name] = now

                elif album.type == "local" and album.path:
                    # Local directory - scan filesystem
                    album_name = album.name or album.path
                    logger.info(f"Scanning local directory: {album_name}")
                    self._sync_progress.album_name = album_name
                    self._sync_progress.urls_found = 0

                    items = self._scan_local_directory(album.path, album_name)
                    local_urls = set()
                    for item in items:
                        item_source_types[item.url] = "local"
                        local_urls.add(item.url)
                    all_items.extend(items)
                    albums_scraped_successfully += 1
                    self._sync_progress.albums_done = albums_scraped_successfully
                    # Record sync timestamp for this album
                    self._album_sync_times[album_name] = now

                    # Clean up orphaned local entries: files that were deleted from disk
                    with self._lock:
                        for cached in self._media.values():
                            if (cached.album_source == album_name
                                    and cached.source_type == "local"
                                    and cached.url not in local_urls
                                    and not cached.deleted):
                                # File no longer in directory scan - mark as deleted
                                cached.deleted = True
                                stats["deleted"] += 1
                                logger.debug(f"Marked orphaned local file as deleted: {cached.url}")

            except Exception as e:
                source_info = album.url if album.type == "google_photos" else album.path
                logger.error(f"Failed to process album {source_info}: {e}")
                stats["errors"] += 1
                self._sync_progress.error_message = str(e)

        logger.info(f"Found {len(all_items)} items ({albums_scraped_successfully}/{total_albums} sources processed)")
        self._sync_progress.urls_found = len(all_items)

        # Track which URLs we've seen
        seen_urls: Set[str] = set()

        # Calculate how many need downloading/indexing
        items_to_process = 0
        for item in all_items:
            media_id = self._get_media_id(item.url)
            if media_id not in self._media or force_full:
                items_to_process += 1

        self._sync_progress.stage = "downloading"
        self._sync_progress.downloads_total = items_to_process
        self._sync_progress.downloads_done = 0

        # Track URLs of newly downloaded photos for caption fetching (Google Photos only)
        new_photo_urls: Set[str] = set()

        def _get_file_mtime(file_path: str) -> Optional[str]:
            """Get file modification time as ISO string."""
            try:
                mtime = os.path.getmtime(file_path)
                return datetime.fromtimestamp(mtime).isoformat()
            except OSError:
                return None

        def _extract_local_path(url: str) -> Optional[str]:
            """Extract local file path from file:// URL."""
            if url.startswith("file://"):
                return url[7:]  # Remove "file://" prefix
            return None

        with self._lock:
            for item in all_items:
                media_id = self._get_media_id(item.url)
                seen_urls.add(item.url)
                source_type = item_source_types.get(item.url, "google_photos")

                existing = self._media.get(media_id)

                if existing and not force_full:
                    # Update last_seen and album_source
                    existing.last_seen = now
                    existing.deleted = False
                    if item.album_name:
                        existing.album_source = item.album_name

                    # For local files, check if file has changed (mtime different)
                    if source_type == "local":
                        local_path = _extract_local_path(item.url)
                        if local_path and os.path.exists(local_path):
                            current_mtime = _get_file_mtime(local_path)
                            if current_mtime and current_mtime != existing.file_mtime:
                                # File has changed - re-extract metadata
                                logger.info(f"Local file changed, re-extracting metadata: {local_path}")
                                try:
                                    metadata = self._metadata_extractor.extract(local_path)
                                    if metadata.date_taken:
                                        existing.exif_date = metadata.date_taken.isoformat()
                                    existing.embedded_caption = metadata.caption
                                    # Store GPS for lazy geocoding, clear old location
                                    existing.gps_latitude = metadata.gps_latitude
                                    existing.gps_longitude = metadata.gps_longitude
                                    existing.location = None  # Will be lazy-geocoded
                                    existing.file_mtime = current_mtime
                                    existing.content_hash = self._get_content_hash(local_path)
                                    # Clear cached display params since image may have changed
                                    existing.display_params = None
                                    existing.cached_faces = None
                                    stats["updated"] += 1
                                except Exception as e:
                                    logger.debug(f"Failed to re-extract metadata: {e}")
                                    stats["unchanged"] += 1
                            else:
                                stats["unchanged"] += 1
                        else:
                            # Local file no longer exists - mark as deleted
                            existing.deleted = True
                            stats["deleted"] += 1
                    else:
                        # Google Photos - check if caption changed (rarely populated during scrape)
                        if item.caption and item.caption != existing.google_caption:
                            existing.google_caption = item.caption
                            stats["updated"] += 1
                        else:
                            stats["unchanged"] += 1
                else:
                    # New item - download or index it
                    if source_type == "local":
                        # Local file - use original path, no download
                        local_path = _extract_local_path(item.url)
                        if not local_path or not os.path.exists(local_path):
                            logger.warning(f"Local file not found: {item.url}")
                            stats["errors"] += 1
                            self._sync_progress.downloads_done += 1
                            continue
                        file_mtime = _get_file_mtime(local_path)
                        logger.info(f"Indexing local file: {os.path.basename(local_path)}")
                    else:
                        # Google Photos - download it
                        local_path = self._download_media(item.url, item.media_type, media_id)
                        file_mtime = None

                    if local_path:
                        # Extract metadata
                        exif_date = None
                        embedded_caption = None
                        gps_latitude = None
                        gps_longitude = None
                        if item.media_type == "photo":
                            try:
                                metadata = self._metadata_extractor.extract(local_path)
                                if metadata.date_taken:
                                    exif_date = metadata.date_taken.isoformat()
                                # Store embedded caption separately
                                embedded_caption = metadata.caption
                                # Store GPS coordinates for lazy geocoding later
                                gps_latitude = metadata.gps_latitude
                                gps_longitude = metadata.gps_longitude
                            except Exception as e:
                                logger.debug(f"Failed to extract metadata: {e}")

                        # Create cache entry
                        cached = CachedMedia(
                            media_id=media_id,
                            url=item.url,
                            local_path=local_path,
                            media_type=item.media_type,
                            embedded_caption=embedded_caption,
                            exif_date=exif_date,
                            album_source=item.album_name or "",
                            download_date=now,
                            last_seen=now,
                            content_hash=self._get_content_hash(local_path),
                            source_type=source_type,
                            file_mtime=file_mtime,
                            display_params=None,  # Computed on demand
                            deleted=False,
                            gps_latitude=gps_latitude,
                            gps_longitude=gps_longitude
                        )
                        self._media[media_id] = cached
                        stats["new"] += 1
                        self._sync_progress.downloads_done += 1

                        # Log progress for new items
                        if self._sync_progress.downloads_done % 10 == 0 or source_type == "local":
                            logger.info(f"Progress: {self._sync_progress.downloads_done}/{self._sync_progress.downloads_total} items processed")

                        # Track new Google Photos items for caption fetching
                        # (local files don't have Google captions)
                        if item.media_type == "photo" and source_type == "google_photos":
                            new_photo_urls.add(item.url)
                    else:
                        stats["errors"] += 1
                        self._sync_progress.downloads_done += 1

        # Fetch captions from Google Photos for new photos or all photos if requested
        # Note: Only applies to Google Photos items, not local files
        urls_needing_captions: Set[str] = set()

        if force_refetch_captions:
            # Force re-fetch ALL Google Photos items (use when extraction logic changed)
            # First reset the google_metadata_fetched flag for Google Photos items
            with self._lock:
                for cached in self._media.values():
                    if (cached.media_type == "photo" and not cached.deleted
                            and cached.source_type == "google_photos"):
                        cached.google_metadata_fetched = False
                        urls_needing_captions.add(cached.url)
                self._save_metadata()
            logger.info(f"Force re-fetching Google metadata for ALL {len(urls_needing_captions)} Google Photos...")
        elif update_all_captions:
            # Fetch metadata only for Google Photos where google_metadata_fetched=False
            # Once fetched (even if empty), we never fetch again
            with self._lock:
                for cached in self._media.values():
                    if (cached.media_type == "photo" and not cached.deleted
                            and cached.source_type == "google_photos"):
                        if not cached.google_metadata_fetched:
                            urls_needing_captions.add(cached.url)
            if urls_needing_captions:
                logger.info(f"Fetching Google metadata for {len(urls_needing_captions)} unfetched Google Photos...")
            else:
                logger.info("All Google Photos already have metadata fetched")
        else:
            # Only fetch captions for newly downloaded photos
            urls_needing_captions = new_photo_urls
            if urls_needing_captions:
                logger.info(f"Fetching captions for {len(urls_needing_captions)} new photos...")

        if urls_needing_captions and albums_scraped_successfully > 0:
            self._sync_progress.stage = "fetching_captions"
            self._sync_progress.downloads_done = 0
            self._sync_progress.downloads_total = len(urls_needing_captions)

            # Counter for batched saves
            captions_since_save = [0]  # Use list to allow modification in nested function

            # Callback to save Google caption, location, and date
            # Note: Caption precedence is now applied at display time, not sync time
            def on_caption_found(url: str, google_caption: Optional[str], google_location: Optional[str] = None, google_date: Optional[str] = None) -> None:
                media_id = self._get_media_id(url)
                with self._lock:
                    if media_id not in self._media:
                        return

                    # Store Google caption separately (not merged with embedded)
                    if google_caption and google_caption != self._media[media_id].google_caption:
                        self._media[media_id].google_caption = google_caption
                        stats["captions_updated"] += 1

                    # Store Google location separately
                    if google_location and google_location != self._media[media_id].google_location:
                        self._media[media_id].google_location = google_location

                    # Store Google date (used as fallback when EXIF date is missing)
                    if google_date and google_date != self._media[media_id].google_date:
                        self._media[media_id].google_date = google_date

                    # Mark as fetched - we've tried to get Google metadata for this photo
                    # This is set even if caption/location/date are empty, so we don't retry
                    self._media[media_id].google_metadata_fetched = True

                    # Save to disk every 10 photos to avoid data loss
                    captions_since_save[0] += 1
                    if captions_since_save[0] >= 10:
                        self._save_metadata()
                        captions_since_save[0] = 0
                        logger.info(f"Saved metadata (captions updated: {stats['captions_updated']})")

            # Group URLs by album for efficient fetching
            # Build mapping: album_name -> set of URLs belonging to that album
            urls_by_album: Dict[str, Set[str]] = {}
            with self._lock:
                for url in urls_needing_captions:
                    media_id = self._get_media_id(url)
                    if media_id in self._media:
                        album_name = self._media[media_id].album_source
                        if album_name not in urls_by_album:
                            urls_by_album[album_name] = set()
                        urls_by_album[album_name].add(url)

            for album in self.config.albums:
                if not album.enabled or not album.url or album.type != "google_photos":
                    continue

                # Only fetch metadata for photos belonging to THIS album
                album_urls = urls_by_album.get(album.name, set())
                if not album_urls:
                    logger.debug(f"No metadata to fetch for album: {album.name}")
                    continue

                logger.info(f"Fetching metadata for {len(album_urls)} photos from album: {album.name}")

                try:
                    # Fetch captions for photos in this album
                    def caption_progress(current: int, total: int) -> None:
                        self._sync_progress.downloads_done = current
                        self._sync_progress.downloads_total = total

                    google_captions = self._scraper.fetch_captions(
                        album.url,
                        album_urls,
                        progress_callback=caption_progress,
                        caption_found_callback=on_caption_found
                    )

                    # Final save for any remaining captions
                    if captions_since_save[0] > 0:
                        with self._lock:
                            self._save_metadata()
                        logger.info(f"Final caption save (total updated: {stats['captions_updated']})")

                except Exception as e:
                    # Save what we have before reporting the error
                    with self._lock:
                        self._save_metadata()
                    logger.warning(f"Failed to fetch captions from album {album.url}: {e}")

        # Mark items not seen as deleted - but ONLY if the scrape looks healthy.
        # This prevents marking everything as deleted when the scraper fails
        # (e.g., ChromeDriver crash, network error, page load timeout).
        #
        # We require finding at least 50% of our current cached items.
        # This handles:
        #   - Complete failures (0 items found)
        #   - Partial failures (only a few items scraped before timeout)
        #   - Normal changes (some photos added/removed from album)
        #
        # Important: Only mark items from ENABLED albums as deleted.
        # Items from disabled albums should be preserved (not scraped, not deleted).
        with self._lock:
            # Get names of enabled albums
            enabled_album_names = {
                album.name or (album.url if album.type == "google_photos" else album.path)
                for album in self.config.albums
                if album.enabled
            }

            current_cached_count = sum(1 for c in self._media.values() if not c.deleted)
            min_required = max(1, int(current_cached_count * 0.5))  # At least 50% of current

            if albums_scraped_successfully > 0 and len(all_items) >= min_required:
                for media_id, cached in self._media.items():
                    # Only consider deletion for items from enabled albums
                    if (cached.url not in seen_urls and not cached.deleted
                            and cached.album_source in enabled_album_names):
                        cached.deleted = True
                        stats["deleted"] += 1
            elif total_albums > 0:
                if len(all_items) < min_required and current_cached_count > 0:
                    logger.warning(
                        f"Skipping deletion check: found {len(all_items)} items but expected "
                        f"at least {min_required} (50% of {current_cached_count} cached). "
                        f"Scrape may have failed - preserving existing cache."
                    )
                elif albums_scraped_successfully == 0:
                    logger.warning(
                        f"Skipping deletion check: all {total_albums} album(s) failed to scrape. "
                        "Existing cached items will be preserved."
                    )

            # Save metadata
            self._save_metadata()

            # Update playlist
            self._rebuild_playlist()

        # Manage cache size
        self._enforce_cache_limit()

        # Build log message
        log_parts = [
            f"{stats['new']} new",
            f"{stats['updated']} updated",
            f"{stats['deleted']} deleted",
            f"{stats['unchanged']} unchanged",
            f"{stats['errors']} errors"
        ]
        if stats['captions_updated'] > 0:
            log_parts.append(f"{stats['captions_updated']} captions fetched")

        logger.info(f"Sync complete: {', '.join(log_parts)}")

        # Mark sync as complete
        self._sync_progress.is_syncing = False
        self._sync_progress.stage = "complete"
        self._sync_progress.completed_at = datetime.now().isoformat()

        return stats

    def _rebuild_playlist(self) -> None:
        """Rebuild the playlist of available media."""
        with self._lock:
            # Get enabled album names
            enabled_albums = {
                album.name or album.url
                for album in self.config.albums
                if album.enabled
            }

            # Get all non-deleted media from enabled albums
            available = [
                media_id for media_id, cached in self._media.items()
                if not cached.deleted
                and os.path.exists(cached.local_path)
                and cached.album_source in enabled_albums
            ]

            # Filter by type if videos disabled
            if not self.config.display.video_enabled:
                available = [
                    media_id for media_id in available
                    if self._media[media_id].media_type == "photo"
                ]

            if self.config.display.order == "random":
                random.shuffle(available)
            elif self.config.display.order == "recency_weighted":
                # Weighted random shuffle favoring recent photos
                # Uses date priority: EXIF date > Google date > file mtime
                cutoff_days = self.config.display.recency_cutoff_years * 365
                min_weight = self.config.display.recency_min_weight
                now = datetime.now()

                def get_photo_date(mid: str) -> datetime:
                    """Get photo date with fallback chain."""
                    media = self._media[mid]
                    # Try EXIF date first
                    if media.exif_date:
                        try:
                            return datetime.fromisoformat(media.exif_date)
                        except ValueError:
                            pass
                    # Fall back to Google Photos date
                    if media.google_date:
                        try:
                            return datetime.fromisoformat(media.google_date)
                        except ValueError:
                            pass
                    # Fall back to file modification time
                    try:
                        return datetime.fromtimestamp(os.path.getmtime(media.local_path))
                    except (OSError, ValueError):
                        return now  # Default to now if no date available

                def get_weight(mid: str) -> float:
                    """Calculate recency weight for a photo."""
                    photo_date = get_photo_date(mid)
                    age_days = (now - photo_date).days
                    if age_days < 0:
                        age_days = 0  # Future dates treated as today
                    if age_days >= cutoff_days:
                        return min_weight
                    # Linear interpolation from 1.0 (today) to min_weight (at cutoff)
                    return 1.0 - (1.0 - min_weight) * (age_days / cutoff_days)

                # Weighted random sampling without replacement
                weights = [get_weight(mid) for mid in available]
                weighted_order = []
                remaining = list(available)
                remaining_weights = list(weights)
                while remaining:
                    # Select one item based on weights
                    selected = random.choices(remaining, weights=remaining_weights, k=1)[0]
                    weighted_order.append(selected)
                    # Remove selected item
                    idx = remaining.index(selected)
                    remaining.pop(idx)
                    remaining_weights.pop(idx)
                available = weighted_order
            elif self.config.display.order == "alphabetical":
                # Sort by filename (basename of local_path)
                available.sort(key=lambda mid: os.path.basename(self._media[mid].local_path).lower())
            else:
                # Chronological - sort by date with fallback chain:
                # 1. EXIF date (from photo metadata)
                # 2. Google Photos date (from DOM scraping)
                # 3. File modification time
                def get_sort_date(mid: str) -> str:
                    media = self._media[mid]
                    # Try EXIF date first
                    if media.exif_date:
                        return media.exif_date
                    # Fall back to Google Photos date
                    if media.google_date:
                        return media.google_date
                    # Fall back to file modification time
                    try:
                        mtime = os.path.getmtime(media.local_path)
                        return datetime.fromtimestamp(mtime).isoformat()
                    except (OSError, ValueError):
                        return ""
                available.sort(key=get_sort_date)

            self._playlist = available
            self._playlist_index = 0

    def has_enabled_albums(self) -> bool:
        """Check if any albums are enabled for display."""
        return any(album.enabled for album in self.config.albums)

    def rebuild_playlist(self) -> None:
        """Public method to rebuild playlist after config changes."""
        self._rebuild_playlist()

    def _enforce_cache_limit(self) -> None:
        """Remove old items if cache exceeds size limit."""
        max_bytes = self.config.cache.max_size_mb * 1024 * 1024

        # Calculate current size
        total_size = 0
        for cached in self._media.values():
            if os.path.exists(cached.local_path):
                total_size += os.path.getsize(cached.local_path)

        if total_size <= max_bytes:
            return

        logger.info(f"Cache size ({total_size / 1024 / 1024:.1f} MB) exceeds limit, cleaning up...")

        # Sort by last_seen (oldest first)
        sorted_items = sorted(
            self._media.values(),
            key=lambda c: c.last_seen or "0"
        )

        with self._lock:
            for cached in sorted_items:
                if total_size <= max_bytes:
                    break

                if os.path.exists(cached.local_path):
                    file_size = os.path.getsize(cached.local_path)
                    try:
                        os.remove(cached.local_path)
                        total_size -= file_size
                        del self._media[cached.media_id]
                        logger.debug(f"Removed {cached.local_path}")
                    except Exception as e:
                        logger.warning(f"Failed to remove {cached.local_path}: {e}")

            self._save_metadata()
            self._rebuild_playlist()

    def get_next_media(self) -> Optional[CachedMedia]:
        """
        Get the next media item for display.

        Returns:
            CachedMedia or None if no media available.
        """
        with self._lock:
            if not self._playlist:
                self._rebuild_playlist()

            if not self._playlist:
                return None

            media_id = self._playlist[self._playlist_index]
            self._playlist_index = (self._playlist_index + 1) % len(self._playlist)

            # Reshuffle when we loop back (for random mode)
            if self._playlist_index == 0 and self.config.display.order == "random":
                random.shuffle(self._playlist)

            return self._media.get(media_id)

    def get_previous_media(self) -> Optional[CachedMedia]:
        """
        Get the previous media item for display.

        Returns:
            CachedMedia or None if no media available.
        """
        with self._lock:
            if not self._playlist:
                self._rebuild_playlist()

            if not self._playlist:
                return None

            # Move back 2 positions (since get_next_media already advanced)
            # This effectively goes back one photo
            self._playlist_index = (self._playlist_index - 2) % len(self._playlist)

            media_id = self._playlist[self._playlist_index]
            self._playlist_index = (self._playlist_index + 1) % len(self._playlist)

            return self._media.get(media_id)

    def get_display_params(
        self,
        cached: CachedMedia,
        screen_width: int,
        screen_height: int
    ) -> DisplayParams:
        """
        Get or compute display parameters for a cached media item.

        Args:
            cached: The cached media item.
            screen_width: Display width.
            screen_height: Display height.

        Returns:
            DisplayParams for this media.
        """
        # Check if we have cached params for this resolution
        if cached.display_params:
            try:
                params = DisplayParams.from_dict(cached.display_params)
                if params.screen_resolution == (screen_width, screen_height):
                    return params
            except Exception:
                pass  # Recompute if invalid

        # Initialize components if needed
        if self._face_detector is None:
            try:
                self._face_detector = FaceDetector()
            except Exception as e:
                logger.warning(f"Face detection unavailable: {e}")

        if self._image_processor is None:
            self._image_processor = ImageProcessor(
                screen_width=screen_width,
                screen_height=screen_height,
                scaling_mode=self.config.scaling.mode,
                smart_crop_method=self.config.scaling.smart_crop_method,
                face_position=self.config.scaling.face_position,
                fallback_crop=self.config.scaling.fallback_crop,
                max_crop_percent=self.config.scaling.max_crop_percent,
                saliency_threshold=self.config.scaling.saliency_threshold,
                saliency_coverage=self.config.scaling.saliency_coverage,
                crop_bias=self.config.scaling.crop_bias,
                background_color=tuple(self.config.scaling.background_color),
                ken_burns_enabled=self.config.ken_burns.enabled,
                ken_burns_zoom_range=tuple(self.config.ken_burns.zoom_range),
                ken_burns_pan_speed=self.config.ken_burns.pan_speed,
                ken_burns_randomize=self.config.ken_burns.randomize
            )

        # Get faces: use cached faces if available, otherwise detect and cache
        faces = []
        if cached.media_type == "photo":
            if cached.cached_faces is not None:
                # Use cached faces (already detected)
                faces = faces_from_dict(cached.cached_faces)
                logger.debug(f"Using {len(faces)} cached faces for {cached.media_id}")
            elif self.config.scaling.face_detection and self._face_detector:
                # Detect faces and cache them
                try:
                    faces = self._face_detector.detect_faces(cached.local_path)
                    # Cache the faces separately from display_params
                    with self._lock:
                        cached.cached_faces = faces_to_dict(faces)
                    logger.debug(f"Detected and cached {len(faces)} faces for {cached.media_id}")
                except Exception as e:
                    logger.debug(f"Face detection failed: {e}")

        # Compute display params
        params = self._image_processor.compute_display_params(
            cached.local_path,
            faces=faces,
            photo_duration=self.config.display.photo_duration_seconds
        )

        # Cache the display params
        with self._lock:
            cached.display_params = params.to_dict()
            self._save_metadata()

        return params

    def get_media_count(self) -> Dict[str, int]:
        """Get counts of cached media by type."""
        with self._lock:
            photos = sum(
                1 for c in self._media.values()
                if c.media_type == "photo" and not c.deleted
            )
            videos = sum(
                1 for c in self._media.values()
                if c.media_type == "video" and not c.deleted
            )
            return {"photos": photos, "videos": videos, "total": photos + videos}

    def get_cache_size_mb(self) -> float:
        """Get total cache size in MB."""
        total = 0
        for cached in self._media.values():
            if os.path.exists(cached.local_path):
                total += os.path.getsize(cached.local_path)
        return total / 1024 / 1024

    def get_all_media(self) -> List[CachedMedia]:
        """Get all non-deleted cached media."""
        with self._lock:
            return [c for c in self._media.values() if not c.deleted]

    def get_album_sync_times(self) -> Dict[str, str]:
        """Get last sync timestamp for each album."""
        with self._lock:
            return self._album_sync_times.copy()

    def update_location(self, media_id: str, location: str) -> None:
        """Update the location for a media item (used by lazy geocoding)."""
        with self._lock:
            if media_id in self._media:
                self._media[media_id].location = location
                self._save_metadata()

    def clear_cache(self) -> None:
        """Clear all cached media."""
        with self._lock:
            for cached in self._media.values():
                if os.path.exists(cached.local_path):
                    try:
                        os.remove(cached.local_path)
                    except Exception:
                        pass
            self._media = {}
            self._playlist = []
            self._save_metadata()
        logger.info("Cache cleared")

    def extract_locations(self, progress_callback=None) -> int:
        """
        Extract locations for existing photos that have GPS but no location.

        Args:
            progress_callback: Optional callback(current, total) for progress updates.

        Returns:
            Number of photos updated with location.
        """
        updated = 0
        photos_to_process = []

        with self._lock:
            for cached in self._media.values():
                if cached.deleted or cached.media_type != "photo":
                    continue
                # Skip if already has location or any caption
                if cached.location or cached.google_caption or cached.embedded_caption:
                    continue
                if os.path.exists(cached.local_path):
                    photos_to_process.append(cached)

        total = len(photos_to_process)
        logger.info(f"Extracting locations for {total} photos without caption...")

        for i, cached in enumerate(photos_to_process):
            if progress_callback:
                progress_callback(i + 1, total)

            try:
                metadata = self._metadata_extractor.extract(cached.local_path)
                if metadata.gps_latitude and metadata.gps_longitude:
                    location = reverse_geocode(
                        metadata.gps_latitude,
                        metadata.gps_longitude
                    )
                    if location:
                        with self._lock:
                            cached.location = location
                        updated += 1
                        logger.debug(f"Location for {cached.media_id}: {location}")
            except Exception as e:
                logger.debug(f"Failed to extract location for {cached.media_id}: {e}")

            # Save periodically
            if updated > 0 and updated % 10 == 0:
                with self._lock:
                    self._save_metadata()
                logger.info(f"Extracted {updated} locations so far...")

        # Final save
        if updated > 0:
            with self._lock:
                self._save_metadata()

        logger.info(f"Extracted locations for {updated} photos")
        return updated

    def extract_embedded_captions(
        self,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> int:
        """
        Re-extract embedded EXIF/IPTC captions from local photo files.

        Use this to populate embedded_caption for photos that were cached
        before the separate embedded_caption field was added.

        Args:
            progress_callback: Optional callback(current, total) for progress.

        Returns:
            Number of photos updated with embedded captions.
        """
        updated = 0
        photos_to_process = []

        with self._lock:
            for cached in self._media.values():
                if cached.deleted or cached.media_type != "photo":
                    continue
                # Process photos that don't have embedded_caption yet
                if cached.embedded_caption:
                    continue
                if os.path.exists(cached.local_path):
                    photos_to_process.append(cached)

        total = len(photos_to_process)
        logger.info(f"Extracting embedded captions for {total} photos...")

        for i, cached in enumerate(photos_to_process):
            if progress_callback:
                progress_callback(i + 1, total)

            try:
                metadata = self._metadata_extractor.extract(cached.local_path)
                if metadata.caption:
                    with self._lock:
                        cached.embedded_caption = metadata.caption
                    updated += 1
                    logger.debug(f"Embedded caption for {cached.media_id}: {metadata.caption[:50]}...")
            except Exception as e:
                logger.debug(f"Failed to extract embedded caption for {cached.media_id}: {e}")

            # Save periodically
            if updated > 0 and updated % 50 == 0:
                with self._lock:
                    self._save_metadata()
                logger.info(f"Extracted {updated} embedded captions so far...")

        # Final save
        if updated > 0:
            with self._lock:
                self._save_metadata()

        logger.info(f"Extracted embedded captions for {updated} photos")
        return updated
