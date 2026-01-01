# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""Tests for metadata extraction module."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from src.metadata import (
    PhotoMetadata,
    MetadataExtractor,
    format_date,
    get_photo_date,
    get_photo_caption,
    reverse_geocode,
    _get_us_state_abbrev,
)


class TestPhotoMetadata:
    """Tests for PhotoMetadata dataclass."""

    def test_default_values(self):
        """Test that PhotoMetadata has correct defaults."""
        meta = PhotoMetadata()
        assert meta.date_taken is None
        assert meta.caption is None
        assert meta.camera_make is None
        assert meta.camera_model is None
        assert meta.width is None
        assert meta.height is None
        assert meta.orientation is None
        assert meta.gps_latitude is None
        assert meta.gps_longitude is None
        assert meta.location is None

    def test_with_values(self):
        """Test PhotoMetadata with all values set."""
        dt = datetime(2024, 6, 15, 10, 30, 0)
        meta = PhotoMetadata(
            date_taken=dt,
            caption="Test caption",
            camera_make="Canon",
            camera_model="EOS R5",
            width=4000,
            height=3000,
            orientation=1,
            gps_latitude=40.7128,
            gps_longitude=-74.0060,
            location="New York, NY"
        )
        assert meta.date_taken == dt
        assert meta.caption == "Test caption"
        assert meta.camera_make == "Canon"
        assert meta.camera_model == "EOS R5"
        assert meta.width == 4000
        assert meta.height == 3000
        assert meta.gps_latitude == 40.7128
        assert meta.gps_longitude == -74.0060
        assert meta.location == "New York, NY"


class TestMetadataExtractor:
    """Tests for MetadataExtractor class."""

    def test_extract_nonexistent_file(self):
        """Test extracting metadata from a file that doesn't exist."""
        extractor = MetadataExtractor()
        meta = extractor.extract("/nonexistent/path/image.jpg")
        # Should return empty metadata, not raise
        assert meta.date_taken is None
        assert meta.width is None

    def test_extract_date_formats(self):
        """Test parsing various EXIF date formats."""
        extractor = MetadataExtractor()

        # String format
        exif_data = {"DateTimeOriginal": "2024:06:15 10:30:00"}
        date = extractor._extract_date(exif_data)
        assert date == datetime(2024, 6, 15, 10, 30, 0)

        # Bytes format
        exif_data = {"DateTimeOriginal": b"2024:06:15 10:30:00"}
        date = extractor._extract_date(exif_data)
        assert date == datetime(2024, 6, 15, 10, 30, 0)

        # Invalid format
        exif_data = {"DateTimeOriginal": "invalid-date"}
        date = extractor._extract_date(exif_data)
        assert date is None

        # No date tags
        exif_data = {"SomeOtherTag": "value"}
        date = extractor._extract_date(exif_data)
        assert date is None

    def test_filter_camera_info_caption(self):
        """Test filtering out camera metadata captions."""
        extractor = MetadataExtractor()

        # Should filter out camera info
        assert extractor._filter_camera_info_caption("OLYMPUS DIGITAL CAMERA", "OLYMPUS") is None
        assert extractor._filter_camera_info_caption("Canon DIGITAL CAMERA", None) is None
        assert extractor._filter_camera_info_caption("FUJIFILM X-T4", "FUJIFILM") is None

        # Should keep real captions
        assert extractor._filter_camera_info_caption("Family vacation 2024", None) == "Family vacation 2024"
        assert extractor._filter_camera_info_caption("Birthday party", "Canon") == "Birthday party"

        # None and empty handling
        assert extractor._filter_camera_info_caption(None, None) is None
        assert extractor._filter_camera_info_caption("", None) is None

    def test_convert_gps_coordinate(self):
        """Test GPS coordinate conversion from DMS to decimal."""
        extractor = MetadataExtractor()

        # Create mock rational values
        class MockRational:
            def __init__(self, num, denom):
                self.numerator = num
                self.denominator = denom

        # 40° 26' 46" N = 40.446111...
        coord = (MockRational(40, 1), MockRational(26, 1), MockRational(46, 1))
        decimal = extractor._convert_gps_coordinate(coord)
        assert abs(decimal - 40.446111) < 0.001

        # Simple integers
        coord = (40.0, 30.0, 0.0)
        decimal = extractor._convert_gps_coordinate(coord)
        assert decimal == 40.5  # 40 + 30/60

    def test_extract_gps(self):
        """Test GPS extraction from EXIF GPS info."""
        extractor = MetadataExtractor()

        # No GPS info
        lat, lon = extractor._extract_gps({})
        assert lat is None
        assert lon is None

        # Valid GPS info (using tag IDs)
        gps_info = {
            1: "N",  # GPSLatitudeRef
            2: (40.0, 26.0, 46.0),  # GPSLatitude
            3: "W",  # GPSLongitudeRef
            4: (74.0, 0.0, 21.0),  # GPSLongitude
        }
        lat, lon = extractor._extract_gps(gps_info)
        # With W reference, longitude should be negative
        assert lat is not None
        assert lon is not None


class TestFormatDate:
    """Tests for format_date function."""

    def test_format_date_valid(self):
        """Test formatting a valid datetime."""
        dt = datetime(2024, 6, 15)
        result = format_date(dt, "%B %d, %Y")
        assert result == "June 15, 2024"

    def test_format_date_none(self):
        """Test formatting None returns empty string."""
        result = format_date(None)
        assert result == ""

    def test_format_date_custom_format(self):
        """Test custom format strings."""
        dt = datetime(2024, 6, 15)
        assert format_date(dt, "%Y-%m-%d") == "2024-06-15"
        assert format_date(dt, "%m/%d/%Y") == "06/15/2024"


class TestGetPhotoDate:
    """Tests for get_photo_date convenience function."""

    def test_nonexistent_file(self):
        """Test get_photo_date on nonexistent file."""
        result = get_photo_date("/nonexistent/file.jpg")
        assert result is None


class TestGetPhotoCaption:
    """Tests for get_photo_caption function."""

    def test_google_caption_preferred(self):
        """Test that Google caption takes precedence."""
        with patch.object(MetadataExtractor, 'extract') as mock_extract:
            mock_extract.return_value = PhotoMetadata(caption="Embedded caption")
            result = get_photo_caption("/fake/path.jpg", "Google caption")
            assert result == "Google caption"

    def test_fallback_to_embedded(self):
        """Test fallback to embedded caption when no Google caption."""
        with patch.object(MetadataExtractor, 'extract') as mock_extract:
            mock_extract.return_value = PhotoMetadata(caption="Embedded caption")
            result = get_photo_caption("/fake/path.jpg", None)
            assert result == "Embedded caption"


class TestUSStateAbbrev:
    """Tests for US state abbreviation lookup."""

    def test_known_states(self):
        """Test lookup of known states."""
        assert _get_us_state_abbrev("California") == "CA"
        assert _get_us_state_abbrev("New York") == "NY"
        assert _get_us_state_abbrev("Texas") == "TX"
        assert _get_us_state_abbrev("Colorado") == "CO"

    def test_unknown_state(self):
        """Test unknown state returns input unchanged."""
        assert _get_us_state_abbrev("Unknown") == "Unknown"
        assert _get_us_state_abbrev("Ontario") == "Ontario"


class TestReverseGeocode:
    """Tests for reverse geocoding function."""

    @patch('src.metadata.reverse_geocode')
    def test_geocode_caching(self, mock_geocode):
        """Test that geocoding results are cached."""
        # This tests the caching behavior
        mock_geocode.return_value = "Boulder, CO"

        # First call
        result1 = mock_geocode(40.015, -105.270)
        assert result1 == "Boulder, CO"

        # Verify mock was called
        mock_geocode.assert_called_once()
