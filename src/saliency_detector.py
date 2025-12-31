# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Saliency detection using U2-Net for smart image cropping.

U2-Net detects visually salient (important) regions in an image,
which includes faces, interesting objects, dramatic landscapes, etc.
This is more comprehensive than face-only detection for smart cropping.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SaliencyRegion:
    """
    Represents a salient region in normalized coordinates (0-1).
    """
    x: float      # Left edge
    y: float      # Top edge
    width: float  # Width
    height: float # Height
    score: float  # Saliency score (0-1)

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "score": self.score
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SaliencyRegion":
        return cls(
            x=data["x"],
            y=data["y"],
            width=data["width"],
            height=data["height"],
            score=data["score"]
        )


class SaliencyDetector:
    """
    Detects salient regions in images using U2-Net.

    U2-Net produces a pixel-wise saliency map indicating the visual
    importance of each region. This is useful for smart cropping that
    preserves not just faces but also important scene elements like
    mountains, buildings, or other focal points.
    """

    # Default model path
    DEFAULT_MODEL_PATH = "/opt/photoloop/models/u2netp.onnx"
    # Fallback for development
    DEV_MODEL_PATH = "models/u2netp.onnx"

    # U2-Net input size
    INPUT_SIZE = 320

    def __init__(
        self,
        model_path: Optional[str] = None,
        threshold: float = 0.5
    ):
        """
        Initialize the saliency detector.

        Args:
            model_path: Path to U2-Net ONNX model file.
            threshold: Saliency threshold (0-1) for region detection.
        """
        self.threshold = threshold
        self.net = None

        # Find model file
        if model_path:
            self.model_path = model_path
        elif os.path.exists(self.DEFAULT_MODEL_PATH):
            self.model_path = self.DEFAULT_MODEL_PATH
        elif os.path.exists(self.DEV_MODEL_PATH):
            self.model_path = self.DEV_MODEL_PATH
        else:
            # Check relative to this file
            src_dir = Path(__file__).parent
            model_file = src_dir.parent / "models" / "u2netp.onnx"
            if model_file.exists():
                self.model_path = str(model_file)
            else:
                self.model_path = None
                logger.warning(
                    "U2-Net model not found. Saliency detection disabled. "
                    "Expected at: %s", self.DEFAULT_MODEL_PATH
                )

    def _load_model(self) -> bool:
        """Load the U2-Net model if not already loaded."""
        if self.net is not None:
            return True

        if not self.model_path or not os.path.exists(self.model_path):
            return False

        try:
            self.net = cv2.dnn.readNetFromONNX(self.model_path)
            logger.info(f"Loaded U2-Net saliency model from {self.model_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load U2-Net model: {e}")
            self.net = None
            return False

    def detect_saliency_map(
        self,
        image_path: str
    ) -> Optional[np.ndarray]:
        """
        Generate a saliency map for an image.

        Args:
            image_path: Path to the image file.

        Returns:
            Saliency map as numpy array (0-1 float values),
            same dimensions as input image, or None if detection fails.
        """
        if not self._load_model():
            return None

        try:
            # Load image
            img = cv2.imread(image_path)
            if img is None:
                logger.error(f"Failed to load image: {image_path}")
                return None

            orig_height, orig_width = img.shape[:2]

            # Preprocess: resize to INPUT_SIZE x INPUT_SIZE
            img_resized = cv2.resize(img, (self.INPUT_SIZE, self.INPUT_SIZE))

            # Normalize to [0, 1] and convert to float32
            img_normalized = img_resized.astype(np.float32) / 255.0

            # U2-Net expects RGB, OpenCV loads BGR
            img_rgb = cv2.cvtColor(img_normalized, cv2.COLOR_BGR2RGB)

            # Normalize with ImageNet mean/std (U2-Net training normalization)
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            img_normalized = (img_rgb - mean) / std

            # Create blob: NCHW format
            blob = cv2.dnn.blobFromImage(
                img_normalized,
                scalefactor=1.0,
                size=(self.INPUT_SIZE, self.INPUT_SIZE),
                mean=(0, 0, 0),
                swapRB=False,
                crop=False
            )

            # Run inference
            self.net.setInput(blob)
            output = self.net.forward()

            # U2-Net outputs multiple scales, take the first (finest) one
            # Output shape is typically (1, 1, H, W)
            if len(output.shape) == 4:
                saliency = output[0, 0]
            else:
                saliency = output[0]

            # Apply sigmoid if values are outside [0, 1]
            if saliency.min() < 0 or saliency.max() > 1:
                saliency = 1 / (1 + np.exp(-saliency))

            # Resize back to original image dimensions
            saliency_resized = cv2.resize(
                saliency,
                (orig_width, orig_height),
                interpolation=cv2.INTER_LINEAR
            )

            return saliency_resized.astype(np.float32)

        except Exception as e:
            logger.error(f"Saliency detection failed: {e}")
            return None

    def detect_salient_regions(
        self,
        image_path: str,
        min_region_size: float = 0.05
    ) -> List[SaliencyRegion]:
        """
        Detect discrete salient regions in an image.

        Args:
            image_path: Path to the image file.
            min_region_size: Minimum region size as fraction of image (0-1).

        Returns:
            List of SaliencyRegion objects.
        """
        saliency_map = self.detect_saliency_map(image_path)
        if saliency_map is None:
            return []

        height, width = saliency_map.shape

        # Threshold the saliency map
        binary = (saliency_map > self.threshold).astype(np.uint8) * 255

        # Find connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            binary, connectivity=8
        )

        regions = []
        min_area = min_region_size * width * height

        # Skip label 0 (background)
        for i in range(1, num_labels):
            x, y, w, h, area = stats[i]

            if area < min_area:
                continue

            # Calculate mean saliency score for this region
            mask = labels == i
            score = float(np.mean(saliency_map[mask]))

            # Convert to normalized coordinates
            regions.append(SaliencyRegion(
                x=x / width,
                y=y / height,
                width=w / width,
                height=h / height,
                score=score
            ))

        # Sort by score (highest first)
        regions.sort(key=lambda r: r.score, reverse=True)

        return regions

    def get_saliency_bounding_box(
        self,
        image_path: str,
        coverage: float = 0.9
    ) -> Optional[Tuple[float, float, float, float]]:
        """
        Get a bounding box that covers the most salient regions.

        Args:
            image_path: Path to the image file.
            coverage: Fraction of total saliency to include (0-1).

        Returns:
            Tuple (x, y, width, height) in normalized coordinates,
            or None if detection fails.
        """
        saliency_map = self.detect_saliency_map(image_path)
        if saliency_map is None:
            return None

        height, width = saliency_map.shape

        # Calculate cumulative saliency
        total_saliency = np.sum(saliency_map)
        if total_saliency < 0.001:
            # No significant saliency detected
            return None

        target_saliency = coverage * total_saliency

        # Find bounding box that captures target saliency
        # Start from the center and expand
        best_box = None
        best_saliency = 0

        # Use threshold-based approach for efficiency
        threshold = self.threshold
        while threshold > 0.1:
            binary = saliency_map > threshold
            coords = np.where(binary)

            if len(coords[0]) > 0:
                y_min, y_max = coords[0].min(), coords[0].max()
                x_min, x_max = coords[1].min(), coords[1].max()

                box_saliency = np.sum(saliency_map[y_min:y_max+1, x_min:x_max+1])

                if box_saliency >= target_saliency:
                    best_box = (
                        x_min / width,
                        y_min / height,
                        (x_max - x_min + 1) / width,
                        (y_max - y_min + 1) / height
                    )
                    break

            threshold -= 0.1

        # Fallback: use entire salient area
        if best_box is None:
            binary = saliency_map > 0.1
            coords = np.where(binary)

            if len(coords[0]) > 0:
                y_min, y_max = coords[0].min(), coords[0].max()
                x_min, x_max = coords[1].min(), coords[1].max()

                best_box = (
                    x_min / width,
                    y_min / height,
                    (x_max - x_min + 1) / width,
                    (y_max - y_min + 1) / height
                )

        return best_box

    def get_optimal_crop_position(
        self,
        image_path: str,
        crop_width: float,
        crop_height: float
    ) -> Tuple[float, float]:
        """
        Find the optimal position for a crop of given size to maximize saliency.

        Args:
            image_path: Path to the image file.
            crop_width: Width of crop in normalized coordinates (0-1).
            crop_height: Height of crop in normalized coordinates (0-1).

        Returns:
            (x, y) position for crop that maximizes covered saliency.
        """
        saliency_map = self.detect_saliency_map(image_path)
        if saliency_map is None:
            # Default to center
            return ((1 - crop_width) / 2, (1 - crop_height) / 2)

        height, width = saliency_map.shape
        crop_w_px = int(crop_width * width)
        crop_h_px = int(crop_height * height)

        # Use integral image for fast saliency computation
        integral = cv2.integral(saliency_map)

        best_x, best_y = 0, 0
        best_score = -1

        # Search with step size for efficiency
        step = max(1, min(crop_w_px, crop_h_px) // 20)

        for y in range(0, height - crop_h_px + 1, step):
            for x in range(0, width - crop_w_px + 1, step):
                # Calculate sum using integral image
                x2, y2 = x + crop_w_px, y + crop_h_px
                score = (
                    integral[y2, x2]
                    - integral[y, x2]
                    - integral[y2, x]
                    + integral[y, x]
                )

                if score > best_score:
                    best_score = score
                    best_x, best_y = x, y

        # Refine with smaller steps around best position
        refined_x, refined_y = best_x, best_y
        for dy in range(-step, step + 1):
            for dx in range(-step, step + 1):
                x = max(0, min(width - crop_w_px, best_x + dx))
                y = max(0, min(height - crop_h_px, best_y + dy))

                x2, y2 = x + crop_w_px, y + crop_h_px
                score = (
                    integral[y2, x2]
                    - integral[y, x2]
                    - integral[y2, x]
                    + integral[y, x]
                )

                if score > best_score:
                    best_score = score
                    refined_x, refined_y = x, y

        return (refined_x / width, refined_y / height)


def get_saliency_center(
    saliency_map: np.ndarray
) -> Tuple[float, float]:
    """
    Calculate the weighted center of a saliency map.

    Args:
        saliency_map: 2D numpy array of saliency values.

    Returns:
        (x, y) center in normalized coordinates (0-1).
    """
    height, width = saliency_map.shape

    # Create coordinate grids
    y_coords, x_coords = np.mgrid[0:height, 0:width]

    # Normalize coordinates
    x_norm = x_coords / width
    y_norm = y_coords / height

    # Calculate weighted center
    total_weight = np.sum(saliency_map)
    if total_weight < 0.001:
        return (0.5, 0.5)  # Default to center

    cx = np.sum(x_norm * saliency_map) / total_weight
    cy = np.sum(y_norm * saliency_map) / total_weight

    return (float(cx), float(cy))
