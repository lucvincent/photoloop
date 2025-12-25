"""
Tests for image processor crop calculations.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestCropCalculations:
    """Test crop region calculations."""

    def test_fill_crop_wider_image(self):
        """Wider image should crop sides in fill mode."""
        from src.image_processor import ImageProcessor

        processor = ImageProcessor(
            screen_width=1920,
            screen_height=1080,
            scaling_mode="fill"
        )

        # Simulate a 4000x2000 image (wider than 16:9 screen)
        with patch('PIL.Image.open') as mock_open:
            mock_img = MagicMock()
            mock_img.size = (4000, 2000)  # 2:1 aspect ratio
            mock_img.__enter__ = MagicMock(return_value=mock_img)
            mock_img.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_img

            params = processor.compute_display_params("/fake/path.jpg", faces=[])

        # Crop width should be less than 1 (cropping sides)
        assert params.crop_region.width < 1.0
        assert params.crop_region.height == 1.0

    def test_fill_crop_taller_image(self):
        """Taller image should crop top/bottom in fill mode."""
        from src.image_processor import ImageProcessor

        processor = ImageProcessor(
            screen_width=1920,
            screen_height=1080,
            scaling_mode="fill"
        )

        # Simulate a 1000x2000 image (portrait, taller than 16:9)
        with patch('PIL.Image.open') as mock_open:
            mock_img = MagicMock()
            mock_img.size = (1000, 2000)  # 1:2 aspect ratio
            mock_img.__enter__ = MagicMock(return_value=mock_img)
            mock_img.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_img

            params = processor.compute_display_params("/fake/path.jpg", faces=[])

        # Crop height should be less than 1 (cropping top/bottom)
        assert params.crop_region.width == 1.0
        assert params.crop_region.height < 1.0

    def test_fit_mode_no_crop(self):
        """Fit mode should not crop."""
        from src.image_processor import ImageProcessor

        processor = ImageProcessor(
            screen_width=1920,
            screen_height=1080,
            scaling_mode="fit"
        )

        with patch('PIL.Image.open') as mock_open:
            mock_img = MagicMock()
            mock_img.size = (4000, 2000)
            mock_img.__enter__ = MagicMock(return_value=mock_img)
            mock_img.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_img

            params = processor.compute_display_params("/fake/path.jpg", faces=[])

        # No cropping in fit mode
        assert params.crop_region.x == 0
        assert params.crop_region.y == 0
        assert params.crop_region.width == 1.0
        assert params.crop_region.height == 1.0

    def test_balanced_mode_limits_crop(self):
        """Balanced mode should limit cropping to max_crop_percent."""
        from src.image_processor import ImageProcessor

        processor = ImageProcessor(
            screen_width=1920,
            screen_height=1080,
            scaling_mode="balanced",
            max_crop_percent=15
        )

        # Very wide image that would require >15% crop in fill mode
        with patch('PIL.Image.open') as mock_open:
            mock_img = MagicMock()
            mock_img.size = (4000, 1000)  # 4:1 aspect ratio (very wide)
            mock_img.__enter__ = MagicMock(return_value=mock_img)
            mock_img.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_img

            params = processor.compute_display_params("/fake/path.jpg", faces=[])

        # Crop should be limited to ~15%
        crop_amount = 1.0 - params.crop_region.width
        assert crop_amount <= 0.16  # Allow small tolerance


class TestFaceAwareCropping:
    """Test face-aware crop positioning."""

    def test_crop_centers_on_faces(self, sample_faces):
        """Crop should position faces in frame."""
        from src.image_processor import ImageProcessor

        processor = ImageProcessor(
            screen_width=1920,
            screen_height=1080,
            scaling_mode="fill"
        )

        # Portrait image with faces
        with patch('PIL.Image.open') as mock_open:
            mock_img = MagicMock()
            mock_img.size = (1000, 2000)  # Portrait
            mock_img.__enter__ = MagicMock(return_value=mock_img)
            mock_img.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_img

            params = processor.compute_display_params(
                "/fake/path.jpg",
                faces=sample_faces
            )

        # Faces should be stored in params
        assert len(params.faces) == len(sample_faces)

        # Crop region should be valid
        assert 0 <= params.crop_region.x <= 1
        assert 0 <= params.crop_region.y <= 1
        assert params.crop_region.x + params.crop_region.width <= 1.0
        assert params.crop_region.y + params.crop_region.height <= 1.0


class TestDisplayParamsSerialization:
    """Test DisplayParams serialization."""

    def test_to_dict_and_back(self, sample_faces):
        """DisplayParams should survive round-trip serialization."""
        from src.image_processor import DisplayParams, CropRegion, KenBurnsAnimation

        original = DisplayParams(
            screen_resolution=(1920, 1080),
            faces=sample_faces,
            crop_region=CropRegion(0.1, 0.2, 0.8, 0.7),
            ken_burns=KenBurnsAnimation(
                start_zoom=1.0,
                end_zoom=1.1,
                start_center=(0.5, 0.5),
                end_center=(0.52, 0.48)
            )
        )

        # Serialize and deserialize
        data = original.to_dict()
        restored = DisplayParams.from_dict(data)

        assert restored.screen_resolution == original.screen_resolution
        assert restored.crop_region.x == original.crop_region.x
        assert restored.crop_region.width == original.crop_region.width
        assert restored.ken_burns.start_zoom == original.ken_burns.start_zoom
        assert len(restored.faces) == len(original.faces)
