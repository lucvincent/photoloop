"""
Face detection for smart cropping.
Uses OpenCV's YuNet DNN-based detector for accurate face detection.
"""

import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Default model path
DEFAULT_MODEL_PATH = "/opt/photoloop/models/face_detection_yunet_2023mar.onnx"
DEV_MODEL_PATH = "/home/luc/photoloop/models/face_detection_yunet_2023mar.onnx"


@dataclass
class FaceRegion:
    """
    Represents a detected face region.
    Coordinates are normalized (0-1) relative to image dimensions.
    """
    x: float      # Left edge (0-1)
    y: float      # Top edge (0-1)
    width: float  # Width (0-1)
    height: float # Height (0-1)
    confidence: float = 1.0  # Detection confidence (0-1)

    @property
    def center_x(self) -> float:
        """Center X coordinate."""
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        """Center Y coordinate."""
        return self.y + self.height / 2

    @property
    def area(self) -> float:
        """Area of the face region."""
        return self.width * self.height

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "confidence": self.confidence
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FaceRegion":
        """Create from dictionary."""
        return cls(
            x=data["x"],
            y=data["y"],
            width=data["width"],
            height=data["height"],
            confidence=data.get("confidence", 1.0)
        )


class FaceDetector:
    """
    Detects faces in images using OpenCV's YuNet DNN detector.

    YuNet is a modern, lightweight face detector that:
    - Works well on CPU (designed for mobile/edge)
    - Handles sunglasses, partial occlusions
    - Provides confidence scores
    - Has low false positive rate
    """

    def __init__(
        self,
        model_path: str = None,
        confidence_threshold: float = 0.7,
        nms_threshold: float = 0.3,
        top_k: int = 50
    ):
        """
        Initialize the face detector.

        Args:
            model_path: Path to YuNet ONNX model file.
            confidence_threshold: Minimum confidence for detections (0-1).
            nms_threshold: Non-max suppression threshold.
            top_k: Maximum number of faces to detect.
        """
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.top_k = top_k

        # Find model file
        if model_path and os.path.exists(model_path):
            self.model_path = model_path
        elif os.path.exists(DEFAULT_MODEL_PATH):
            self.model_path = DEFAULT_MODEL_PATH
        elif os.path.exists(DEV_MODEL_PATH):
            self.model_path = DEV_MODEL_PATH
        else:
            raise RuntimeError(
                f"YuNet model not found. Please download it to {DEFAULT_MODEL_PATH}\n"
                "curl -L -o /opt/photoloop/models/face_detection_yunet_2023mar.onnx "
                "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
            )

        # Detector will be initialized per-image (needs image dimensions)
        self._detector = None
        self._current_size = None

        logger.debug(f"Face detector initialized with model: {self.model_path}")

    def _get_detector(self, width: int, height: int) -> cv2.FaceDetectorYN:
        """Get or create detector for given image size."""
        if self._detector is None or self._current_size != (width, height):
            self._detector = cv2.FaceDetectorYN.create(
                self.model_path,
                "",
                (width, height),
                self.confidence_threshold,
                self.nms_threshold,
                self.top_k
            )
            self._current_size = (width, height)
        return self._detector

    def detect_faces(
        self,
        image_path: str,
        min_face_size: float = 0.02
    ) -> List[FaceRegion]:
        """
        Detect faces in an image.

        Args:
            image_path: Path to the image file.
            min_face_size: Minimum face size as fraction of image (0-1).

        Returns:
            List of FaceRegion objects (normalized coordinates).
        """
        try:
            # Load image with OpenCV
            img = cv2.imread(image_path)
            if img is None:
                logger.warning(f"Could not load image: {image_path}")
                return []

            return self._detect_faces_impl(img, min_face_size)

        except Exception as e:
            logger.error(f"Error detecting faces in {image_path}: {e}")
            return []

    def _detect_faces_impl(
        self,
        img: np.ndarray,
        min_face_size: float = 0.02
    ) -> List[FaceRegion]:
        """Internal face detection implementation."""
        img_height, img_width = img.shape[:2]

        # Get detector for this image size
        detector = self._get_detector(img_width, img_height)

        # Detect faces
        _, faces = detector.detect(img)

        if faces is None:
            return []

        # Convert to FaceRegion objects
        regions = []
        min_size_pixels = min_face_size * min(img_width, img_height)

        for face in faces:
            # YuNet returns: x, y, w, h, landmarks..., confidence
            x, y, w, h = face[0:4]
            confidence = face[14]  # Last element is confidence

            # Skip small faces
            if w < min_size_pixels or h < min_size_pixels:
                continue

            # Normalize coordinates
            norm_x = x / img_width
            norm_y = y / img_height
            norm_w = w / img_width
            norm_h = h / img_height

            region = FaceRegion(
                x=float(norm_x),
                y=float(norm_y),
                width=float(norm_w),
                height=float(norm_h),
                confidence=float(confidence)
            )
            regions.append(region)

        logger.debug(f"Detected {len(regions)} faces (confidence >= {self.confidence_threshold})")
        return regions

    def detect_faces_from_pil(
        self,
        pil_image: Image.Image,
        min_face_size: float = 0.02
    ) -> List[FaceRegion]:
        """
        Detect faces in a PIL Image.

        Args:
            pil_image: PIL Image object.
            min_face_size: Minimum face size as fraction of image.

        Returns:
            List of FaceRegion objects (normalized coordinates).
        """
        try:
            # Convert PIL to OpenCV format
            img_array = np.array(pil_image)

            # Handle different color modes
            if len(img_array.shape) == 2:
                # Grayscale -> BGR
                img = cv2.cvtColor(img_array, cv2.COLOR_GRAY2BGR)
            elif img_array.shape[2] == 3:
                # RGB -> BGR
                img = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
            elif img_array.shape[2] == 4:
                # RGBA -> BGR
                img = cv2.cvtColor(img_array, cv2.COLOR_RGBA2BGR)
            else:
                logger.warning(f"Unexpected image shape: {img_array.shape}")
                return []

            return self._detect_faces_impl(img, min_face_size)

        except Exception as e:
            logger.error(f"Error detecting faces: {e}")
            return []


def get_faces_bounding_box(
    faces: List[FaceRegion],
    margin: float = 0.1
) -> Optional[Tuple[float, float, float, float]]:
    """
    Get a bounding box that contains all detected faces.

    Args:
        faces: List of FaceRegion objects.
        margin: Margin to add around the bounding box (0-1).

    Returns:
        Tuple of (x, y, width, height) normalized, or None if no faces.
    """
    if not faces:
        return None

    # Find the bounding box of all faces
    min_x = min(f.x for f in faces)
    min_y = min(f.y for f in faces)
    max_x = max(f.x + f.width for f in faces)
    max_y = max(f.y + f.height for f in faces)

    # Add margin
    width = max_x - min_x
    height = max_y - min_y

    min_x = max(0, min_x - width * margin)
    min_y = max(0, min_y - height * margin)
    max_x = min(1, max_x + width * margin)
    max_y = min(1, max_y + height * margin)

    return (min_x, min_y, max_x - min_x, max_y - min_y)


def get_faces_center(faces: List[FaceRegion]) -> Optional[Tuple[float, float]]:
    """
    Get the weighted center of all detected faces.
    Larger faces (closer to camera) have more weight.

    Args:
        faces: List of FaceRegion objects.

    Returns:
        Tuple of (center_x, center_y) normalized, or None if no faces.
    """
    if not faces:
        return None

    # Weight by area (larger faces are more important)
    total_weight = sum(f.area for f in faces)

    if total_weight == 0:
        # Fallback to simple average
        center_x = sum(f.center_x for f in faces) / len(faces)
        center_y = sum(f.center_y for f in faces) / len(faces)
    else:
        center_x = sum(f.center_x * f.area for f in faces) / total_weight
        center_y = sum(f.center_y * f.area for f in faces) / total_weight

    return (center_x, center_y)


def faces_to_dict(faces: List[FaceRegion]) -> List[dict]:
    """Convert list of FaceRegion to list of dicts for serialization."""
    return [f.to_dict() for f in faces]


def faces_from_dict(data: List[dict]) -> List[FaceRegion]:
    """Convert list of dicts to list of FaceRegion."""
    return [FaceRegion.from_dict(d) for d in data]
