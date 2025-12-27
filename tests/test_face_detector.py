# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Tests for face detection utilities.
"""

import pytest


class TestFaceSerialization:
    """Test face detection serialization helpers."""

    def test_faces_to_dict(self, sample_faces):
        """faces_to_dict should serialize FaceRegion list."""
        from src.face_detector import faces_to_dict

        result = faces_to_dict(sample_faces)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["x"] == 0.3
        assert result[0]["confidence"] == 0.95

    def test_faces_from_dict(self):
        """faces_from_dict should deserialize to FaceRegion list."""
        from src.face_detector import faces_from_dict, FaceRegion

        data = [
            {"x": 0.3, "y": 0.2, "width": 0.1, "height": 0.15, "confidence": 0.95},
            {"x": 0.6, "y": 0.25, "width": 0.08, "height": 0.12, "confidence": 0.87},
        ]

        result = faces_from_dict(data)

        assert len(result) == 2
        assert isinstance(result[0], FaceRegion)
        assert result[0].x == 0.3
        assert result[1].confidence == 0.87

    def test_round_trip_serialization(self, sample_faces):
        """Faces should survive round-trip serialization."""
        from src.face_detector import faces_to_dict, faces_from_dict

        serialized = faces_to_dict(sample_faces)
        restored = faces_from_dict(serialized)

        assert len(restored) == len(sample_faces)
        for original, restored_face in zip(sample_faces, restored):
            assert original.x == restored_face.x
            assert original.y == restored_face.y
            assert original.width == restored_face.width
            assert original.height == restored_face.height
            assert original.confidence == restored_face.confidence

    def test_empty_faces(self):
        """Empty face list should serialize correctly."""
        from src.face_detector import faces_to_dict, faces_from_dict

        assert faces_to_dict([]) == []
        assert faces_from_dict([]) == []


class TestFaceBoundingBox:
    """Test face bounding box calculations."""

    def test_get_faces_bounding_box(self, sample_faces):
        """get_faces_bounding_box should return combined bbox."""
        from src.face_detector import get_faces_bounding_box

        bbox = get_faces_bounding_box(sample_faces, margin=0)

        assert bbox is not None
        x, y, w, h = bbox

        # Should encompass both faces
        assert x <= 0.3  # Left edge of first face
        assert y <= 0.2  # Top edge of first face
        # Right edge should include second face
        assert x + w >= 0.6 + 0.08

    def test_bounding_box_with_margin(self, sample_faces):
        """Bounding box should expand with margin."""
        from src.face_detector import get_faces_bounding_box

        bbox_no_margin = get_faces_bounding_box(sample_faces, margin=0)
        bbox_with_margin = get_faces_bounding_box(sample_faces, margin=0.05)

        # With margin should be larger
        assert bbox_with_margin[2] >= bbox_no_margin[2]  # Width
        assert bbox_with_margin[3] >= bbox_no_margin[3]  # Height

    def test_empty_faces_returns_none(self):
        """Empty face list should return None."""
        from src.face_detector import get_faces_bounding_box

        assert get_faces_bounding_box([], margin=0) is None
