"""
Metadata extraction for photos.
Extracts EXIF data, IPTC captions, and formats dates.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

logger = logging.getLogger(__name__)


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

        # Fallback to file modification time if no EXIF date
        if not metadata.date_taken:
            try:
                mtime = os.path.getmtime(image_path)
                metadata.date_taken = datetime.fromtimestamp(mtime)
            except Exception:
                pass

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
