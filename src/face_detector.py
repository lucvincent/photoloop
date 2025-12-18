"""
Face detection for smart cropping.
Uses OpenCV's Haar Cascade classifier for lightweight CPU-based detection.
"""

import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


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
            "height": self.height
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FaceRegion":
        """Create from dictionary."""
        return cls(
            x=data["x"],
            y=data["y"],
            width=data["width"],
            height=data["height"]
        )


class FaceDetector:
    """
    Detects faces in images using OpenCV Haar Cascades.

    This is a lightweight detector that works well on Raspberry Pi
    without requiring a GPU.
    """

    def __init__(self):
        """Initialize the face detector with Haar cascade classifiers."""
        # Load the pre-trained face cascade
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"

        if not os.path.exists(cascade_path):
            raise RuntimeError(
                f"Haar cascade file not found at {cascade_path}. "
                "Ensure OpenCV is properly installed."
            )

        self.face_cascade = cv2.CascadeClassifier(cascade_path)

        # Optional: eye cascade for better face verification
        eye_cascade_path = cv2.data.haarcascades + "haarcascade_eye.xml"
        if os.path.exists(eye_cascade_path):
            self.eye_cascade = cv2.CascadeClassifier(eye_cascade_path)
        else:
            self.eye_cascade = None

        logger.debug("Face detector initialized")

    def detect_faces(
        self,
        image_path: str,
        min_size: Tuple[int, int] = (30, 30),
        scale_factor: float = 1.1,
        min_neighbors: int = 5
    ) -> List[FaceRegion]:
        """
        Detect faces in an image.

        Args:
            image_path: Path to the image file.
            min_size: Minimum face size in pixels (width, height).
            scale_factor: Scale factor for multi-scale detection.
            min_neighbors: Minimum neighbors for detection confidence.

        Returns:
            List of FaceRegion objects (normalized coordinates).
        """
        try:
            # Load image with OpenCV
            img = cv2.imread(image_path)
            if img is None:
                logger.warning(f"Could not load image: {image_path}")
                return []

            # Get image dimensions
            img_height, img_width = img.shape[:2]

            # Convert to grayscale
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # Detect faces
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=scale_factor,
                minNeighbors=min_neighbors,
                minSize=min_size,
                flags=cv2.CASCADE_SCALE_IMAGE
            )

            # Convert to normalized FaceRegion objects
            regions = []
            for (x, y, w, h) in faces:
                region = FaceRegion(
                    x=x / img_width,
                    y=y / img_height,
                    width=w / img_width,
                    height=h / img_height
                )
                regions.append(region)

            logger.debug(f"Detected {len(regions)} faces in {image_path}")
            return regions

        except Exception as e:
            logger.error(f"Error detecting faces in {image_path}: {e}")
            return []

    def detect_faces_from_pil(
        self,
        pil_image: Image.Image,
        min_size: Tuple[int, int] = (30, 30),
        scale_factor: float = 1.1,
        min_neighbors: int = 5
    ) -> List[FaceRegion]:
        """
        Detect faces in a PIL Image.

        Args:
            pil_image: PIL Image object.
            min_size: Minimum face size in pixels.
            scale_factor: Scale factor for multi-scale detection.
            min_neighbors: Minimum neighbors for detection confidence.

        Returns:
            List of FaceRegion objects (normalized coordinates).
        """
        try:
            # Convert PIL to OpenCV format
            img_array = np.array(pil_image)

            # Handle different color modes
            if len(img_array.shape) == 2:
                # Already grayscale
                gray = img_array
            elif img_array.shape[2] == 3:
                # RGB -> BGR -> Gray
                img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            elif img_array.shape[2] == 4:
                # RGBA -> RGB -> BGR -> Gray
                img_rgb = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
                img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
                gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            else:
                logger.warning(f"Unexpected image shape: {img_array.shape}")
                return []

            # Get dimensions
            img_height, img_width = gray.shape[:2]

            # Detect faces
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=scale_factor,
                minNeighbors=min_neighbors,
                minSize=min_size,
                flags=cv2.CASCADE_SCALE_IMAGE
            )

            # Convert to normalized FaceRegion objects
            regions = []
            for (x, y, w, h) in faces:
                region = FaceRegion(
                    x=x / img_width,
                    y=y / img_height,
                    width=w / img_width,
                    height=h / img_height
                )
                regions.append(region)

            return regions

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
