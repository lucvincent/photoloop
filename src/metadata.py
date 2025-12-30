# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Metadata extraction for photos.
Extracts EXIF data, IPTC captions, and formats dates.
Includes reverse geocoding for GPS coordinates.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

logger = logging.getLogger(__name__)

# Geocoding cache to avoid repeated API calls
_geocode_cache: dict = {}
_geocode_cache_lock = threading.Lock()
_geocode_cache_path: Optional[Path] = None
_last_geocode_time: float = 0


@dataclass
class PhotoMetadata:
    """Extracted metadata from a photo."""
    date_taken: Optional[datetime] = None
    caption: Optional[str] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    orientation: Optional[int] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    location: Optional[str] = None  # Human-readable location from reverse geocoding


class MetadataExtractor:
    """Extracts metadata from photo files."""

    # EXIF date format
    EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"

    # EXIF tags we're interested in
    DATE_TAGS = [
        "DateTimeOriginal",     # When photo was taken
        "DateTimeDigitized",    # When photo was digitized
        "DateTime",             # File modification time
    ]

    def extract(self, image_path: str) -> PhotoMetadata:
        """
        Extract metadata from an image file.

        Args:
            image_path: Path to the image file.

        Returns:
            PhotoMetadata with extracted information.
        """
        metadata = PhotoMetadata()

        if not os.path.exists(image_path):
            logger.warning(f"File not found: {image_path}")
            return metadata

        try:
            with Image.open(image_path) as img:
                # Get image dimensions
                metadata.width, metadata.height = img.size

                # Extract EXIF data
                exif_data = self._get_exif_data(img)

                if exif_data:
                    # Extract date
                    metadata.date_taken = self._extract_date(exif_data)

                    # Extract camera info
                    metadata.camera_make = exif_data.get("Make")
                    metadata.camera_model = exif_data.get("Model")

                    # Extract orientation
                    metadata.orientation = exif_data.get("Orientation")

                    # Extract GPS
                    gps_info = exif_data.get("GPSInfo")
                    if gps_info:
                        metadata.gps_latitude, metadata.gps_longitude = (
                            self._extract_gps(gps_info)
                        )

                # Try to extract IPTC caption
                metadata.caption = self._extract_iptc_caption(img)

                # Fallback: try XMP description
                if not metadata.caption:
                    metadata.caption = self._extract_xmp_description(img)

        except Exception as e:
            logger.warning(f"Error extracting metadata from {image_path}: {e}")

        # NOTE: We intentionally do NOT fall back to file modification time.
        # For downloaded photos, file mtime would show the download date,
        # not when the photo was actually taken. It's better to show no date
        # than a misleading one.

        return metadata

    def _get_exif_data(self, img: Image.Image) -> dict:
        """
        Extract EXIF data as a dictionary with readable tag names.

        Args:
            img: PIL Image object.

        Returns:
            Dictionary of EXIF data.
        """
        exif_data = {}

        try:
            raw_exif = img._getexif()
            if raw_exif:
                for tag_id, value in raw_exif.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    exif_data[tag_name] = value
        except Exception as e:
            logger.debug(f"Error reading EXIF: {e}")

        return exif_data

    def _extract_date(self, exif_data: dict) -> Optional[datetime]:
        """
        Extract the date the photo was taken from EXIF data.

        Args:
            exif_data: Dictionary of EXIF data.

        Returns:
            datetime or None if not found.
        """
        for tag in self.DATE_TAGS:
            date_str = exif_data.get(tag)
            if date_str:
                try:
                    # Handle string dates
                    if isinstance(date_str, str):
                        return datetime.strptime(date_str, self.EXIF_DATE_FORMAT)
                    # Handle bytes
                    elif isinstance(date_str, bytes):
                        return datetime.strptime(
                            date_str.decode('utf-8'),
                            self.EXIF_DATE_FORMAT
                        )
                except ValueError as e:
                    logger.debug(f"Failed to parse date '{date_str}': {e}")

        return None

    def _extract_gps(self, gps_info: dict) -> Tuple[Optional[float], Optional[float]]:
        """
        Extract GPS coordinates from EXIF GPS info.

        Args:
            gps_info: GPS info dictionary from EXIF.

        Returns:
            Tuple of (latitude, longitude) or (None, None).
        """
        try:
            # Decode GPS tag names
            gps_data = {}
            for tag_id, value in gps_info.items():
                tag_name = GPSTAGS.get(tag_id, tag_id)
                gps_data[tag_name] = value

            # Extract latitude
            lat = gps_data.get("GPSLatitude")
            lat_ref = gps_data.get("GPSLatitudeRef")

            # Extract longitude
            lon = gps_data.get("GPSLongitude")
            lon_ref = gps_data.get("GPSLongitudeRef")

            if lat and lon:
                latitude = self._convert_gps_coordinate(lat)
                longitude = self._convert_gps_coordinate(lon)

                if lat_ref == "S":
                    latitude = -latitude
                if lon_ref == "W":
                    longitude = -longitude

                return latitude, longitude

        except Exception as e:
            logger.debug(f"Error extracting GPS: {e}")

        return None, None

    def _convert_gps_coordinate(self, coord) -> float:
        """
        Convert GPS coordinate from degrees/minutes/seconds to decimal.

        Args:
            coord: GPS coordinate tuple (degrees, minutes, seconds).

        Returns:
            Decimal degrees.
        """
        def to_float(val):
            if hasattr(val, 'numerator'):
                return float(val.numerator) / float(val.denominator)
            return float(val)

        degrees = to_float(coord[0])
        minutes = to_float(coord[1])
        seconds = to_float(coord[2])

        return degrees + (minutes / 60.0) + (seconds / 3600.0)

    def _extract_iptc_caption(self, img: Image.Image) -> Optional[str]:
        """
        Extract caption from IPTC metadata.

        Args:
            img: PIL Image object.

        Returns:
            Caption string or None.
        """
        try:
            # Try to get IPTC data
            from PIL import IptcImagePlugin
            iptc = IptcImagePlugin.getiptcinfo(img)

            if iptc:
                # Caption/Abstract is tag (2, 120)
                caption = iptc.get((2, 120))
                if caption:
                    if isinstance(caption, bytes):
                        return caption.decode('utf-8', errors='ignore')
                    elif isinstance(caption, list):
                        return caption[0].decode('utf-8', errors='ignore')
                    return str(caption)

                # Also try headline (2, 105)
                headline = iptc.get((2, 105))
                if headline:
                    if isinstance(headline, bytes):
                        return headline.decode('utf-8', errors='ignore')
                    return str(headline)

        except Exception as e:
            logger.debug(f"Error extracting IPTC: {e}")

        return None

    def _extract_xmp_description(self, img: Image.Image) -> Optional[str]:
        """
        Extract description from XMP metadata.

        Args:
            img: PIL Image object.

        Returns:
            Description string or None.
        """
        try:
            # Check for XMP data in image info
            xmp_data = img.info.get('xmp')
            if xmp_data:
                # Simple regex to find description
                import re
                if isinstance(xmp_data, bytes):
                    xmp_data = xmp_data.decode('utf-8', errors='ignore')

                # Look for dc:description
                match = re.search(r'<dc:description>.*?<rdf:li[^>]*>([^<]+)</rdf:li>', xmp_data)
                if match:
                    return match.group(1).strip()

                # Look for dc:title as fallback
                match = re.search(r'<dc:title>.*?<rdf:li[^>]*>([^<]+)</rdf:li>', xmp_data)
                if match:
                    return match.group(1).strip()

        except Exception as e:
            logger.debug(f"Error extracting XMP: {e}")

        return None


def format_date(date: Optional[datetime], format_string: str = "%B %d, %Y") -> str:
    """
    Format a datetime for display.

    Args:
        date: datetime to format, or None.
        format_string: strftime format string.

    Returns:
        Formatted date string, or empty string if date is None.
    """
    if date is None:
        return ""

    try:
        return date.strftime(format_string)
    except Exception:
        return ""


def get_photo_date(image_path: str) -> Optional[datetime]:
    """
    Convenience function to get just the photo date.

    Args:
        image_path: Path to image file.

    Returns:
        datetime when photo was taken, or None.
    """
    extractor = MetadataExtractor()
    metadata = extractor.extract(image_path)
    return metadata.date_taken


def get_photo_caption(
    image_path: str,
    google_caption: Optional[str] = None
) -> Optional[str]:
    """
    Get the best available caption for a photo.

    Prefers Google Photos caption if available, falls back to embedded metadata.

    Args:
        image_path: Path to image file.
        google_caption: Caption from Google Photos (if any).

    Returns:
        Caption string or None.
    """
    # Prefer Google Photos caption
    if google_caption:
        return google_caption

    # Fall back to embedded metadata
    extractor = MetadataExtractor()
    metadata = extractor.extract(image_path)
    return metadata.caption


def init_geocode_cache(cache_dir: str) -> None:
    """
    Initialize the geocoding cache from disk.

    Args:
        cache_dir: Directory where cache file is stored.
    """
    global _geocode_cache, _geocode_cache_path

    _geocode_cache_path = Path(cache_dir) / "geocode_cache.json"

    with _geocode_cache_lock:
        if _geocode_cache_path.exists():
            try:
                with open(_geocode_cache_path, 'r') as f:
                    _geocode_cache = json.load(f)
                logger.info(f"Loaded {len(_geocode_cache)} cached geocode results")
            except Exception as e:
                logger.warning(f"Failed to load geocode cache: {e}")
                _geocode_cache = {}
        else:
            _geocode_cache = {}


def _save_geocode_cache() -> None:
    """Save geocoding cache to disk."""
    global _geocode_cache, _geocode_cache_path

    if _geocode_cache_path is None:
        return

    with _geocode_cache_lock:
        try:
            with open(_geocode_cache_path, 'w') as f:
                json.dump(_geocode_cache, f)
        except Exception as e:
            logger.warning(f"Failed to save geocode cache: {e}")


def reverse_geocode(latitude: float, longitude: float) -> Optional[str]:
    """
    Convert GPS coordinates to a human-readable location name.

    Uses OpenStreetMap's Nominatim service with rate limiting and caching.

    Args:
        latitude: GPS latitude in decimal degrees.
        longitude: GPS longitude in decimal degrees.

    Returns:
        Location string like "Boulder, CO" or "Paris, France", or None on error.
    """
    global _geocode_cache, _last_geocode_time

    # Round coordinates to ~100m precision for cache efficiency
    # 3 decimal places ≈ 111m precision
    cache_key = f"{latitude:.3f},{longitude:.3f}"

    # Check cache first
    with _geocode_cache_lock:
        if cache_key in _geocode_cache:
            return _geocode_cache[cache_key]

    # Rate limit: Nominatim requires max 1 request per second
    with _geocode_cache_lock:
        elapsed = time.time() - _last_geocode_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _last_geocode_time = time.time()

    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderTimedOut, GeocoderServiceError

        geolocator = Nominatim(
            user_agent="photoloop/1.0 (raspberry pi photo frame)",
            timeout=10
        )

        location = geolocator.reverse(
            (latitude, longitude),
            exactly_one=True,
            language="en"
        )

        if location and location.raw:
            address = location.raw.get("address", {})

            # Extract relevant components
            city = (
                address.get("city") or
                address.get("town") or
                address.get("village") or
                address.get("municipality") or
                address.get("county")
            )

            state = address.get("state")
            country = address.get("country")
            country_code = address.get("country_code", "").upper()

            # Format based on country
            if country_code == "US" and city and state:
                # US: "Boulder, CO" - use state abbreviation
                state_abbrev = _get_us_state_abbrev(state)
                result = f"{city}, {state_abbrev}"
            elif city and country:
                # International: "Paris, France"
                result = f"{city}, {country}"
            elif city:
                result = city
            elif country:
                result = country
            else:
                result = None

            # Cache the result
            with _geocode_cache_lock:
                _geocode_cache[cache_key] = result
                # Save cache periodically (every 10 new entries)
                if len(_geocode_cache) % 10 == 0:
                    _save_geocode_cache()

            return result

    except ImportError:
        logger.warning("geopy not installed, cannot reverse geocode")
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        logger.debug(f"Geocoding service error: {e}")
    except Exception as e:
        logger.debug(f"Reverse geocoding failed: {e}")

    # Cache failures too to avoid repeated API calls
    with _geocode_cache_lock:
        _geocode_cache[cache_key] = None

    return None


def _get_us_state_abbrev(state_name: str) -> str:
    """Convert US state name to abbreviation."""
    states = {
        "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
        "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
        "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
        "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
        "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
        "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
        "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
        "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
        "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
        "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
        "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
        "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
        "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC"
    }
    return states.get(state_name, state_name)
