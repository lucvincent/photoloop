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
    cached_faces: Optional[List[Dict[str, Any]]] = None  # Detected faces (separate from display_params)
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
            "cached_faces": self.cached_faces,
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
            cached_faces=data.get("cached_faces"),
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
        self._sync_lock = threading.Lock()  # Prevent concurrent syncs

        # In-memory cache of metadata
        self._media: Dict[str, CachedMedia] = {}

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
                temp_path = self.metadata_path + '.tmp'
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

    def sync(self, force_full: bool = False, update_all_captions: bool = False) -> Dict[str, int]:
        """
        Sync cache with Google Photos albums.

        Args:
            force_full: Force re-download of all items.
            update_all_captions: If True, fetch captions for all photos (not just new ones).

        Returns:
            Statistics dict with counts of new/updated/deleted items.
        """
        stats = {"new": 0, "updated": 0, "deleted": 0, "unchanged": 0, "errors": 0}

        # Prevent concurrent syncs
        if not self._sync_lock.acquire(blocking=False):
            logger.warning("Sync already in progress, skipping")
            return stats

        try:
            return self._do_sync(force_full, update_all_captions)
        finally:
            self._sync_lock.release()

    def _do_sync(self, force_full: bool = False, update_all_captions: bool = False) -> Dict[str, int]:
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

        # Scrape all configured albums
        all_items: List[MediaItem] = []
        albums_scraped_successfully = 0
        total_albums = sum(1 for a in self.config.albums if a.url)
        self._sync_progress.albums_total = total_albums

        for album in self.config.albums:
            if not album.url:
                continue
            try:
                album_name = album.name or album.url
                logger.info(f"Scraping album: {album_name}")
                self._sync_progress.album_name = album_name
                self._sync_progress.urls_found = 0

                items = self._scraper.scrape_album(album.url)
                for item in items:
                    item.caption = item.caption  # Keep original caption
                all_items.extend(items)
                albums_scraped_successfully += 1
                self._sync_progress.albums_done = albums_scraped_successfully
            except Exception as e:
                logger.error(f"Failed to scrape album {album.url}: {e}")
                stats["errors"] += 1
                self._sync_progress.error_message = str(e)

        logger.info(f"Found {len(all_items)} items in albums ({albums_scraped_successfully}/{total_albums} albums scraped)")
        self._sync_progress.urls_found = len(all_items)

        # Track which URLs we've seen
        seen_urls: Set[str] = set()

        # Calculate how many need downloading
        items_to_download = 0
        for item in all_items:
            media_id = self._get_media_id(item.url)
            if media_id not in self._media or force_full:
                items_to_download += 1

        self._sync_progress.stage = "downloading"
        self._sync_progress.downloads_total = items_to_download
        self._sync_progress.downloads_done = 0

        # Track URLs of newly downloaded photos for caption fetching
        new_photo_urls: Set[str] = set()

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
                        embedded_caption = None
                        if item.media_type == "photo":
                            try:
                                metadata = self._metadata_extractor.extract(local_path)
                                if metadata.date_taken:
                                    exif_date = metadata.date_taken.isoformat()
                                # Store embedded caption separately
                                embedded_caption = metadata.caption
                            except Exception as e:
                                logger.debug(f"Failed to extract metadata: {e}")

                        # Create cache entry
                        # For now, use embedded caption; Google Photos caption will be fetched later
                        # and applied based on caption_precedence config
                        cached = CachedMedia(
                            media_id=media_id,
                            url=item.url,
                            local_path=local_path,
                            media_type=item.media_type,
                            caption=embedded_caption,  # Start with embedded, may be updated
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
                        self._sync_progress.downloads_done += 1

                        # Always track new photos for Google Photos caption fetching
                        if item.media_type == "photo":
                            new_photo_urls.add(item.url)
                    else:
                        stats["errors"] += 1
                        self._sync_progress.downloads_done += 1

        # Fetch captions from Google Photos for new photos or all photos if requested
        urls_needing_captions: Set[str] = set()
        # Track embedded captions so we can apply precedence later
        embedded_captions: Dict[str, Optional[str]] = {}

        if update_all_captions:
            # Fetch captions for all photos
            with self._lock:
                for cached in self._media.values():
                    if cached.media_type == "photo" and not cached.deleted:
                        urls_needing_captions.add(cached.url)
                        embedded_captions[cached.url] = cached.caption
            logger.info(f"Updating captions for all {len(urls_needing_captions)} photos...")
        else:
            # Only fetch captions for newly downloaded photos
            urls_needing_captions = new_photo_urls
            # Track embedded captions for new photos
            with self._lock:
                for url in new_photo_urls:
                    media_id = self._get_media_id(url)
                    if media_id in self._media:
                        embedded_captions[url] = self._media[media_id].caption
            if urls_needing_captions:
                logger.info(f"Fetching captions for {len(urls_needing_captions)} new photos...")

        # Get caption precedence setting
        caption_precedence = getattr(self.config.sync, 'caption_precedence', 'google_photos')

        if urls_needing_captions and albums_scraped_successfully > 0:
            self._sync_progress.stage = "fetching_captions"

            # Counter for batched saves
            captions_since_save = [0]  # Use list to allow modification in nested function

            # Callback to save each caption immediately
            def on_caption_found(url: str, google_caption: Optional[str]) -> None:
                media_id = self._get_media_id(url)
                with self._lock:
                    if media_id not in self._media:
                        return

                    embedded_caption = embedded_captions.get(url)

                    # Apply caption based on precedence setting
                    if caption_precedence == "google_photos":
                        final_caption = google_caption or embedded_caption
                    else:
                        final_caption = embedded_caption or google_caption

                    if final_caption and final_caption != self._media[media_id].caption:
                        self._media[media_id].caption = final_caption
                        stats["captions_updated"] += 1

                    # Save to disk every 10 captions to avoid data loss
                    captions_since_save[0] += 1
                    if captions_since_save[0] >= 10:
                        self._save_metadata()
                        captions_since_save[0] = 0
                        logger.info(f"Saved metadata (captions updated: {stats['captions_updated']})")

            # Group URLs by album for efficient fetching
            for album in self.config.albums:
                if not album.url:
                    continue

                try:
                    # Fetch captions for photos in this album
                    def caption_progress(current: int, total: int) -> None:
                        self._sync_progress.downloads_done = current
                        self._sync_progress.downloads_total = total

                    google_captions = self._scraper.fetch_captions(
                        album.url,
                        urls_needing_captions,
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
        with self._lock:
            current_cached_count = sum(1 for c in self._media.values() if not c.deleted)
            min_required = max(1, int(current_cached_count * 0.5))  # At least 50% of current

            if albums_scraped_successfully > 0 and len(all_items) >= min_required:
                for media_id, cached in self._media.items():
                    if cached.url not in seen_urls and not cached.deleted:
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
                max_crop_percent=self.config.scaling.max_crop_percent,
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
