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
from typing import Any, Dict, List, Optional, Set
import random

import requests
from PIL import Image

from .album_scraper import AlbumScraper, MediaItem
from .config import PhotoLoopConfig
from .face_detector import FaceDetector, FaceRegion, faces_from_dict, faces_to_dict
from .image_processor import DisplayParams, ImageProcessor
from .metadata import MetadataExtractor

logger = logging.getLogger(__name__)


@dataclass
class CachedMedia:
    """Metadata for a cached media item."""
    media_id: str                    # Hash of original URL
    url: str                         # Original Google Photos URL
    local_path: str                  # Path to cached file
    media_type: str                  # "photo" or "video"
    caption: Optional[str] = None    # From Google Photos
    exif_date: Optional[str] = None  # ISO format date string
    album_source: str = ""           # Which album it came from
    download_date: str = ""          # When first downloaded
    last_seen: str = ""              # Last time seen in album scrape
    content_hash: str = ""           # Hash of file content
    display_params: Optional[Dict[str, Any]] = None  # Cached display parameters
    deleted: bool = False            # Marked for deletion

    def to_dict(self) -> dict:
        return {
            "media_id": self.media_id,
            "url": self.url,
            "local_path": self.local_path,
            "media_type": self.media_type,
            "caption": self.caption,
            "exif_date": self.exif_date,
            "album_source": self.album_source,
            "download_date": self.download_date,
            "last_seen": self.last_seen,
            "content_hash": self.content_hash,
            "display_params": self.display_params,
            "deleted": self.deleted
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CachedMedia":
        return cls(
            media_id=data["media_id"],
            url=data["url"],
            local_path=data["local_path"],
            media_type=data["media_type"],
            caption=data.get("caption"),
            exif_date=data.get("exif_date"),
            album_source=data.get("album_source", ""),
            download_date=data.get("download_date", ""),
            last_seen=data.get("last_seen", ""),
            content_hash=data.get("content_hash", ""),
            display_params=data.get("display_params"),
            deleted=data.get("deleted", False)
        )


class CacheManager:
    """
    Manages the local cache of photos and videos.

    Handles:
    - Downloading media from Google Photos
    - Storing metadata in JSON database
    - Incremental sync (only download new/changed items)
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

        # In-memory cache of metadata
        self._media: Dict[str, CachedMedia] = {}

        # Components
        self._scraper = AlbumScraper(headless=True)
        self._face_detector: Optional[FaceDetector] = None
        self._metadata_extractor = MetadataExtractor()
        self._image_processor: Optional[ImageProcessor] = None

        # Playback state
        self._playlist: List[str] = []
        self._playlist_index: int = 0

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Load existing metadata
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load metadata from disk."""
        with self._lock:
            if self.metadata_path.exists():
                try:
                    with open(self.metadata_path, 'r') as f:
                        data = json.load(f)
                    self._media = {
                        k: CachedMedia.from_dict(v)
                        for k, v in data.get("media", {}).items()
                    }
                    logger.info(f"Loaded {len(self._media)} cached items from metadata")
                except Exception as e:
                    logger.error(f"Failed to load metadata: {e}")
                    self._media = {}

    def _save_metadata(self) -> None:
        """Save metadata to disk."""
        with self._lock:
            try:
                data = {
                    "media": {k: v.to_dict() for k, v in self._media.items()},
                    "last_updated": datetime.now().isoformat()
                }
                with open(self.metadata_path, 'w') as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to save metadata: {e}")

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

    def sync(self, force_full: bool = False) -> Dict[str, int]:
        """
        Sync cache with Google Photos albums.

        Args:
            force_full: Force re-download of all items.

        Returns:
            Statistics dict with counts of new/updated/deleted items.
        """
        stats = {"new": 0, "updated": 0, "deleted": 0, "unchanged": 0, "errors": 0}

        logger.info("Starting album sync...")

        # Get current time for last_seen
        now = datetime.now().isoformat()

        # Scrape all configured albums
        all_items: List[MediaItem] = []
        for album in self.config.albums:
            if not album.url:
                continue
            try:
                logger.info(f"Scraping album: {album.name or album.url}")
                items = self._scraper.scrape_album(album.url)
                for item in items:
                    item.caption = item.caption  # Keep original caption
                all_items.extend(items)
            except Exception as e:
                logger.error(f"Failed to scrape album {album.url}: {e}")
                stats["errors"] += 1

        logger.info(f"Found {len(all_items)} items in albums")

        # Track which URLs we've seen
        seen_urls: Set[str] = set()

        with self._lock:
            for item in all_items:
                media_id = self._get_media_id(item.url)
                seen_urls.add(item.url)

                existing = self._media.get(media_id)

                if existing and not force_full:
                    # Update last_seen
                    existing.last_seen = now
                    existing.deleted = False

                    # Check if caption changed
                    if item.caption and item.caption != existing.caption:
                        existing.caption = item.caption
                        stats["updated"] += 1
                    else:
                        stats["unchanged"] += 1
                else:
                    # New item - download it
                    local_path = self._download_media(item.url, item.media_type, media_id)

                    if local_path:
                        # Extract metadata
                        exif_date = None
                        if item.media_type == "photo":
                            try:
                                metadata = self._metadata_extractor.extract(local_path)
                                if metadata.date_taken:
                                    exif_date = metadata.date_taken.isoformat()

                                # Get caption from EXIF if not from Google
                                if not item.caption and metadata.caption:
                                    item.caption = metadata.caption
                            except Exception as e:
                                logger.debug(f"Failed to extract metadata: {e}")

                        # Create cache entry
                        cached = CachedMedia(
                            media_id=media_id,
                            url=item.url,
                            local_path=local_path,
                            media_type=item.media_type,
                            caption=item.caption,
                            exif_date=exif_date,
                            album_source=self.config.albums[0].name if self.config.albums else "",
                            download_date=now,
                            last_seen=now,
                            content_hash=self._get_content_hash(local_path),
                            display_params=None,  # Computed on demand
                            deleted=False
                        )
                        self._media[media_id] = cached
                        stats["new"] += 1
                    else:
                        stats["errors"] += 1

            # Mark items not seen as deleted
            for media_id, cached in self._media.items():
                if cached.url not in seen_urls and not cached.deleted:
                    cached.deleted = True
                    stats["deleted"] += 1

            # Save metadata
            self._save_metadata()

            # Update playlist
            self._rebuild_playlist()

        # Manage cache size
        self._enforce_cache_limit()

        logger.info(
            f"Sync complete: {stats['new']} new, {stats['updated']} updated, "
            f"{stats['deleted']} deleted, {stats['unchanged']} unchanged, "
            f"{stats['errors']} errors"
        )

        return stats

    def _rebuild_playlist(self) -> None:
        """Rebuild the playlist of available media."""
        with self._lock:
            # Get all non-deleted media
            available = [
                media_id for media_id, cached in self._media.items()
                if not cached.deleted and os.path.exists(cached.local_path)
            ]

            # Filter by type if videos disabled
            if not self.config.display.video_enabled:
                available = [
                    media_id for media_id in available
                    if self._media[media_id].media_type == "photo"
                ]

            if self.config.display.order == "random":
                random.shuffle(available)
            else:
                # Sequential - sort by date
                available.sort(key=lambda mid: self._media[mid].exif_date or "")

            self._playlist = available
            self._playlist_index = 0

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
                face_position=self.config.scaling.face_position,
                fallback_crop=self.config.scaling.fallback_crop,
                ken_burns_enabled=self.config.ken_burns.enabled,
                ken_burns_zoom_range=tuple(self.config.ken_burns.zoom_range),
                ken_burns_pan_speed=self.config.ken_burns.pan_speed,
                ken_burns_randomize=self.config.ken_burns.randomize
            )

        # Detect faces if enabled and not cached
        faces = []
        if (self.config.scaling.face_detection and
            self._face_detector and
            cached.media_type == "photo"):
            try:
                faces = self._face_detector.detect_faces(cached.local_path)
            except Exception as e:
                logger.debug(f"Face detection failed: {e}")

        # Compute display params
        params = self._image_processor.compute_display_params(
            cached.local_path,
            faces=faces,
            photo_duration=self.config.display.photo_duration_seconds
        )

        # Cache the params
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
