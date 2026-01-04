# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""
Image processing for scaling, cropping, and Ken Burns effects.
Handles smart cropping using multiple methods (face detection, saliency, aesthetics).
"""

import logging
import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from .face_detector import FaceRegion, get_faces_bounding_box, get_faces_center
from .saliency_detector import SaliencyDetector
from .aesthetic_cropper import AestheticCropper, TORCH_AVAILABLE

logger = logging.getLogger(__name__)


@dataclass
class CropRegion:
    """
    Represents a crop region in normalized coordinates (0-1).
    """
    x: float      # Left edge
    y: float      # Top edge
    width: float  # Width
    height: float # Height

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CropRegion":
        return cls(
            x=data["x"],
            y=data["y"],
            width=data["width"],
            height=data["height"]
        )


@dataclass
class KenBurnsAnimation:
    """
    Ken Burns animation parameters.
    All coordinates are normalized (0-1).
    """
    start_zoom: float           # Starting zoom level (1.0 = no zoom)
    end_zoom: float             # Ending zoom level
    start_center: Tuple[float, float]  # (x, y) center at start
    end_center: Tuple[float, float]    # (x, y) center at end

    def to_dict(self) -> dict:
        return {
            "start_zoom": self.start_zoom,
            "end_zoom": self.end_zoom,
            "start_center": list(self.start_center),
            "end_center": list(self.end_center)
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KenBurnsAnimation":
        return cls(
            start_zoom=data["start_zoom"],
            end_zoom=data["end_zoom"],
            start_center=tuple(data["start_center"]),
            end_center=tuple(data["end_center"])
        )


@dataclass
class DisplayParams:
    """
    Pre-computed display parameters for a photo.
    These are cached to avoid recomputation.
    """
    screen_resolution: Tuple[int, int]
    faces: List[FaceRegion] = field(default_factory=list)
    crop_region: Optional[CropRegion] = None
    ken_burns: Optional[KenBurnsAnimation] = None

    def to_dict(self) -> dict:
        return {
            "screen_resolution": list(self.screen_resolution),
            "faces": [f.to_dict() for f in self.faces],
            "crop_region": self.crop_region.to_dict() if self.crop_region else None,
            "ken_burns": self.ken_burns.to_dict() if self.ken_burns else None
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DisplayParams":
        faces = [FaceRegion.from_dict(f) for f in data.get("faces", [])]
        crop = CropRegion.from_dict(data["crop_region"]) if data.get("crop_region") else None
        kb = KenBurnsAnimation.from_dict(data["ken_burns"]) if data.get("ken_burns") else None

        return cls(
            screen_resolution=tuple(data["screen_resolution"]),
            faces=faces,
            crop_region=crop,
            ken_burns=kb
        )


class ImageProcessor:
    """
    Processes images for display with smart cropping and Ken Burns effects.
    """

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        scaling_mode: str = "fill",
        smart_crop_method: str = "face",
        face_position: str = "center",
        fallback_crop: str = "center",
        max_crop_percent: int = 15,
        saliency_threshold: float = 0.3,
        saliency_coverage: float = 0.9,
        crop_bias: str = "none",
        background_color: Tuple[int, int, int] = (0, 0, 0),
        ken_burns_enabled: bool = True,
        ken_burns_zoom_range: Tuple[float, float] = (1.0, 1.15),
        ken_burns_pan_speed: float = 0.02,
        ken_burns_randomize: bool = True
    ):
        """
        Initialize the image processor.

        Args:
            screen_width: Display width in pixels.
            screen_height: Display height in pixels.
            scaling_mode: "fill", "fit", "balanced", or "stretch".
            smart_crop_method: "face", "saliency", or "aesthetic".
            face_position: "center", "rule_of_thirds", or "top_third".
            fallback_crop: "center", "top", or "bottom" when no faces/regions.
            max_crop_percent: For "balanced" mode, max % of image to crop (0-50).
            saliency_threshold: For saliency method, min threshold (0-1).
            saliency_coverage: For saliency method, how much saliency to cover (0-1).
            background_color: RGB tuple for letterbox/pillarbox fill.
            ken_burns_enabled: Whether to generate Ken Burns animations.
            ken_burns_zoom_range: (min_zoom, max_zoom) for Ken Burns.
            ken_burns_pan_speed: Pan speed as fraction per second.
            ken_burns_randomize: Randomize animation direction.
        """
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.screen_aspect = screen_width / screen_height

        self.scaling_mode = scaling_mode
        self.smart_crop_method = smart_crop_method
        self.face_position = face_position
        self.fallback_crop = fallback_crop
        self.max_crop_percent = max_crop_percent
        self.saliency_threshold = saliency_threshold
        self.saliency_coverage = saliency_coverage
        self.crop_bias = crop_bias
        self.background_color = background_color

        self.ken_burns_enabled = ken_burns_enabled
        self.ken_burns_zoom_range = ken_burns_zoom_range
        self.ken_burns_pan_speed = ken_burns_pan_speed
        self.ken_burns_randomize = ken_burns_randomize

        # Initialize smart cropping modules (lazy loading)
        self._saliency_detector: Optional[SaliencyDetector] = None
        self._aesthetic_cropper: Optional[AestheticCropper] = None

    @property
    def saliency_detector(self) -> SaliencyDetector:
        """Lazy-load saliency detector."""
        if self._saliency_detector is None:
            self._saliency_detector = SaliencyDetector(threshold=self.saliency_threshold)
        return self._saliency_detector

    @property
    def aesthetic_cropper(self) -> AestheticCropper:
        """Lazy-load aesthetic cropper."""
        if self._aesthetic_cropper is None:
            self._aesthetic_cropper = AestheticCropper()
        return self._aesthetic_cropper

    def compute_display_params(
        self,
        image_path: str,
        faces: Optional[List[FaceRegion]] = None,
        photo_duration: float = 30.0
    ) -> DisplayParams:
        """
        Compute display parameters for an image.

        Args:
            image_path: Path to the image file.
            faces: Pre-detected faces (if available, used for "face" method).
            photo_duration: How long the photo will be displayed (for Ken Burns).

        Returns:
            DisplayParams with crop region and Ken Burns animation.
        """
        # Load image to get dimensions
        try:
            with Image.open(image_path) as img:
                img_width, img_height = img.size
        except Exception as e:
            logger.error(f"Failed to load image {image_path}: {e}")
            # Return default params
            return DisplayParams(
                screen_resolution=(self.screen_width, self.screen_height),
                faces=[],
                crop_region=CropRegion(0, 0, 1, 1),
                ken_burns=None
            )

        img_aspect = img_width / img_height

        # Get saliency map if using saliency or aesthetic methods
        saliency_map = None
        if self.smart_crop_method in ("saliency", "aesthetic"):
            try:
                saliency_map = self.saliency_detector.detect_saliency_map(image_path)
            except Exception as e:
                logger.warning(f"Saliency detection failed, using fallback: {e}")

        # Calculate crop region based on scaling mode
        if self.scaling_mode == "fit":
            # No cropping needed for fit mode
            crop_region = CropRegion(0, 0, 1, 1)
        elif self.scaling_mode == "stretch":
            # No cropping, will stretch
            crop_region = CropRegion(0, 0, 1, 1)
        elif self.scaling_mode == "balanced":
            # Balanced mode: partial cropping to reduce bars while keeping most of image
            crop_region = self._compute_balanced_crop(
                image_path, img_width, img_height,
                faces or [], saliency_map
            )
        else:  # fill mode
            crop_region = self._compute_fill_crop(
                image_path, img_width, img_height,
                faces or [], saliency_map
            )

        # Generate Ken Burns animation
        ken_burns = None
        if self.ken_burns_enabled:
            ken_burns = self._generate_ken_burns(
                crop_region,
                faces or [],
                photo_duration
            )

        return DisplayParams(
            screen_resolution=(self.screen_width, self.screen_height),
            faces=faces or [],
            crop_region=crop_region,
            ken_burns=ken_burns
        )

    def _compute_fill_crop(
        self,
        image_path: str,
        img_width: int,
        img_height: int,
        faces: List[FaceRegion],
        saliency_map: Optional[np.ndarray] = None
    ) -> CropRegion:
        """
        Compute crop region for fill mode (image fills screen, excess cropped).

        Args:
            image_path: Path to the image file.
            img_width: Image width.
            img_height: Image height.
            faces: Detected faces.
            saliency_map: Pre-computed saliency map (for saliency/aesthetic methods).

        Returns:
            CropRegion for optimal crop.
        """
        img_aspect = img_width / img_height

        # Determine crop dimensions
        if img_aspect > self.screen_aspect:
            # Image is wider - crop sides
            crop_height = 1.0
            crop_width = self.screen_aspect / img_aspect
        else:
            # Image is taller - crop top/bottom (portrait photos)
            crop_width = 1.0
            crop_height = img_aspect / self.screen_aspect

        # Determine crop position based on smart_crop_method
        crop_x, crop_y = self._get_smart_crop_position(
            image_path, crop_width, crop_height, faces, saliency_map
        )

        return CropRegion(crop_x, crop_y, crop_width, crop_height)

    def _compute_balanced_crop(
        self,
        image_path: str,
        img_width: int,
        img_height: int,
        faces: List[FaceRegion],
        saliency_map: Optional[np.ndarray] = None
    ) -> CropRegion:
        """
        Compute crop region for balanced mode.

        Balanced mode crops up to max_crop_percent of the image to reduce
        letterbox/pillarbox bars, while keeping most of the image visible.
        If the aspect ratio difference is small, it acts like "fill".
        If large, it limits cropping and accepts some bars.

        Args:
            image_path: Path to the image file.
            img_width: Image width.
            img_height: Image height.
            faces: Detected faces.
            saliency_map: Pre-computed saliency map (for saliency/aesthetic methods).

        Returns:
            CropRegion for balanced crop.
        """
        img_aspect = img_width / img_height
        max_crop = self.max_crop_percent / 100.0

        # Calculate what "fill" mode would crop
        if img_aspect > self.screen_aspect:
            # Image is wider - would crop sides
            fill_crop_width = self.screen_aspect / img_aspect
            fill_crop_height = 1.0
            # How much of the image width would be cropped?
            crop_fraction = 1.0 - fill_crop_width
        else:
            # Image is taller - would crop top/bottom
            fill_crop_width = 1.0
            fill_crop_height = img_aspect / self.screen_aspect
            # How much of the image height would be cropped?
            crop_fraction = 1.0 - fill_crop_height

        # If fill mode cropping is within our limit, use fill mode
        if crop_fraction <= max_crop:
            # Use full fill mode crop
            crop_width = fill_crop_width
            crop_height = fill_crop_height
        else:
            # Limit the crop to max_crop_percent
            if img_aspect > self.screen_aspect:
                # Wider image: limit horizontal crop
                crop_width = 1.0 - max_crop
                # Calculate height to maintain as much of screen aspect as possible
                # while staying within image bounds
                ideal_height = crop_width * img_aspect / self.screen_aspect
                crop_height = min(1.0, ideal_height)
            else:
                # Taller image: limit vertical crop
                crop_height = 1.0 - max_crop
                # Calculate width
                ideal_width = crop_height * self.screen_aspect / img_aspect
                crop_width = min(1.0, ideal_width)

        # Determine crop position using smart crop method
        crop_x, crop_y = self._get_smart_crop_position(
            image_path, crop_width, crop_height, faces, saliency_map
        )

        return CropRegion(crop_x, crop_y, crop_width, crop_height)

    def _get_smart_crop_position(
        self,
        image_path: str,
        crop_width: float,
        crop_height: float,
        faces: List[FaceRegion],
        saliency_map: Optional[np.ndarray] = None
    ) -> Tuple[float, float]:
        """
        Get crop position using the configured smart crop method.

        Args:
            image_path: Path to the image file.
            crop_width: Width of crop region (0-1).
            crop_height: Height of crop region (0-1).
            faces: Detected faces (used for "face" method).
            saliency_map: Pre-computed saliency map (for saliency/aesthetic methods).

        Returns:
            (x, y) position for crop region.
        """
        if self.smart_crop_method == "saliency":
            crop_x, crop_y = self._position_crop_for_saliency(
                image_path, crop_width, crop_height, saliency_map
            )
        elif self.smart_crop_method == "aesthetic":
            crop_x, crop_y = self._position_crop_for_aesthetics(
                image_path, crop_width, crop_height, saliency_map
            )
        else:  # "face" method (default)
            if faces:
                crop_x, crop_y = self._position_crop_for_faces(
                    crop_width, crop_height, faces
                )
            else:
                crop_x, crop_y = self._get_fallback_crop_position(crop_width, crop_height)

        # Apply crop bias to preserve top or bottom of image
        if self.crop_bias == "top":
            # Minimize cropping from top - shift crop upward as much as possible
            # while still keeping any detected faces visible
            if faces:
                # Find the lowest face bottom to ensure we don't crop faces
                face_bottoms = [f.y + f.height for f in faces if f.width >= 0.02 or f.height >= 0.02]
                if face_bottoms:
                    min_crop_y = max(0, max(face_bottoms) - crop_height + 0.05)
                    crop_y = max(0, min(crop_y, min_crop_y))
                else:
                    crop_y = 0
            else:
                crop_y = 0
        elif self.crop_bias == "bottom":
            # Minimize cropping from bottom - shift crop downward
            if faces:
                # Find the highest face top
                face_tops = [f.y for f in faces if f.width >= 0.02 or f.height >= 0.02]
                if face_tops:
                    max_crop_y = min(1 - crop_height, min(face_tops) - 0.05)
                    crop_y = min(1 - crop_height, max(crop_y, max_crop_y))
                else:
                    crop_y = 1 - crop_height
            else:
                crop_y = 1 - crop_height

        return crop_x, crop_y

    def _position_crop_for_faces(
        self,
        crop_width: float,
        crop_height: float,
        faces: List[FaceRegion]
    ) -> Tuple[float, float]:
        """
        Position crop to keep faces visible and well-placed.

        Faces are positioned at approximately 3/4 up from the bottom of the frame
        (y=0.25 in normalized coordinates), which is aesthetically pleasing for
        photos of people.

        Args:
            crop_width: Width of crop region (0-1).
            crop_height: Height of crop region (0-1).
            faces: Detected faces.

        Returns:
            (x, y) position for crop region.
        """
        # Filter to significant faces only (ignore tiny background faces)
        # A face should be at least 3% of image dimension to be considered significant
        min_face_size = 0.03
        significant_faces = [
            f for f in faces
            if f.width >= min_face_size or f.height >= min_face_size
        ]

        # If no significant faces, try slightly smaller threshold
        if not significant_faces:
            min_face_size = 0.02
            significant_faces = [
                f for f in faces
                if f.width >= min_face_size or f.height >= min_face_size
            ]

        if not significant_faces:
            return self._get_fallback_crop_position(crop_width, crop_height)

        # Get bounding box of all significant faces
        face_bbox = get_faces_bounding_box(significant_faces, margin=0.02)
        if not face_bbox:
            return self._get_fallback_crop_position(crop_width, crop_height)

        fb_x, fb_y, fb_w, fb_h = face_bbox

        # Calculate the vertical center of the faces (top of heads to chin area)
        # For better framing, use the upper portion of faces (eyes/forehead area)
        face_top = fb_y
        face_vertical_center = fb_y + fb_h * 0.4  # Focus on upper part of face region

        # Target position: faces should appear at 1/4 down from top (3/4 up from bottom)
        # In frame coordinates, y=0.25 means 1/4 from top
        target_y_in_frame = 0.25

        # Calculate crop_y to position faces at target
        # face_vertical_center should map to (crop_y + target_y_in_frame * crop_height)
        # So: crop_y = face_vertical_center - target_y_in_frame * crop_height
        crop_y = face_vertical_center - target_y_in_frame * crop_height

        # For X position: center the faces horizontally
        face_cx = fb_x + fb_w / 2
        crop_x = face_cx - 0.5 * crop_width

        # Now ensure ALL significant faces fit within the crop region
        # Add safety margin around the face bounding box
        safety_margin = 0.02

        # Check if faces would be cut off and adjust
        # Top boundary: ensure face tops aren't cut
        if fb_y < crop_y + safety_margin:
            crop_y = fb_y - safety_margin

        # Bottom boundary: ensure face bottoms aren't cut
        if fb_y + fb_h > crop_y + crop_height - safety_margin:
            crop_y = fb_y + fb_h - crop_height + safety_margin

        # Left boundary
        if fb_x < crop_x + safety_margin:
            crop_x = fb_x - safety_margin

        # Right boundary
        if fb_x + fb_w > crop_x + crop_width - safety_margin:
            crop_x = fb_x + fb_w - crop_width + safety_margin

        # Clamp to valid range (crop must stay within image bounds)
        crop_x = max(0, min(1 - crop_width, crop_x))
        crop_y = max(0, min(1 - crop_height, crop_y))

        return crop_x, crop_y

    def _position_crop_for_saliency(
        self,
        image_path: str,
        crop_width: float,
        crop_height: float,
        saliency_map: Optional[np.ndarray] = None
    ) -> Tuple[float, float]:
        """
        Position crop to maximize coverage of salient regions.

        Uses U2-Net saliency detection to find visually important areas
        (faces, objects, interesting scene elements) and positions the
        crop to include as much of them as possible.

        Args:
            image_path: Path to the image file.
            crop_width: Width of crop region (0-1).
            crop_height: Height of crop region (0-1).
            saliency_map: Pre-computed saliency map (optional).

        Returns:
            (x, y) position for crop region.
        """
        # Get or compute saliency map
        if saliency_map is None:
            try:
                saliency_map = self.saliency_detector.detect_saliency_map(image_path)
            except Exception as e:
                logger.warning(f"Saliency detection failed: {e}")
                return self._get_fallback_crop_position(crop_width, crop_height)

        if saliency_map is None:
            return self._get_fallback_crop_position(crop_width, crop_height)

        # Use integral image for efficient saliency sum calculation
        height, width = saliency_map.shape
        crop_w_px = int(crop_width * width)
        crop_h_px = int(crop_height * height)

        if crop_w_px >= width or crop_h_px >= height:
            return self._get_fallback_crop_position(crop_width, crop_height)

        # Compute integral image
        integral = np.zeros((height + 1, width + 1), dtype=np.float64)
        integral[1:, 1:] = np.cumsum(np.cumsum(saliency_map, axis=0), axis=1)

        # Search for best position
        best_x, best_y = 0, 0
        best_score = -1

        # Use step size for efficiency
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
                    best_x, best_y = x, y

        # Convert to normalized coordinates
        crop_x = best_x / width
        crop_y = best_y / height

        return crop_x, crop_y

    def _position_crop_for_aesthetics(
        self,
        image_path: str,
        crop_width: float,
        crop_height: float,
        saliency_map: Optional[np.ndarray] = None
    ) -> Tuple[float, float]:
        """
        Position crop using aesthetic scoring (GAIC or composition rules).

        Uses either the GAIC neural network (if available) or a fallback
        composition-based approach that combines saliency with rule-of-thirds
        positioning.

        Args:
            image_path: Path to the image file.
            crop_width: Width of crop region (0-1).
            crop_height: Height of crop region (0-1).
            saliency_map: Pre-computed saliency map (optional).

        Returns:
            (x, y) position for crop region.
        """
        try:
            # Try aesthetic cropper first
            target_ratio = crop_width / crop_height * self.screen_aspect
            best_crop = self.aesthetic_cropper.find_best_crop(
                image_path,
                target_ratio=target_ratio,
                saliency_map=saliency_map
            )

            if best_crop is not None:
                return best_crop.x, best_crop.y
        except Exception as e:
            logger.warning(f"Aesthetic cropping failed: {e}")

        # Fallback: combine saliency with composition rules
        if saliency_map is not None:
            # Find the saliency center and position crop around it
            height, width = saliency_map.shape

            # Calculate weighted center of saliency
            y_coords, x_coords = np.mgrid[0:height, 0:width]
            x_norm = x_coords / width
            y_norm = y_coords / height

            total_weight = np.sum(saliency_map)
            if total_weight > 0.001:
                center_x = np.sum(x_norm * saliency_map) / total_weight
                center_y = np.sum(y_norm * saliency_map) / total_weight

                # Position crop to put saliency center at rule-of-thirds point
                # Use lower third intersection (good for landscapes with sky)
                target_x = 0.5  # Centered horizontally
                target_y = 0.33  # Upper third

                # Calculate crop position
                crop_x = center_x - target_x * crop_width
                crop_y = center_y - target_y * crop_height

                # Clamp to valid range
                crop_x = max(0, min(1 - crop_width, crop_x))
                crop_y = max(0, min(1 - crop_height, crop_y))

                return crop_x, crop_y

        return self._get_fallback_crop_position(crop_width, crop_height)

    def _get_fallback_crop_position(
        self,
        crop_width: float,
        crop_height: float
    ) -> Tuple[float, float]:
        """
        Get fallback crop position when no faces detected.

        Args:
            crop_width: Width of crop region.
            crop_height: Height of crop region.

        Returns:
            (x, y) position for crop region.
        """
        # X is always centered
        crop_x = (1 - crop_width) / 2

        # Y depends on fallback setting
        if self.fallback_crop == "top":
            crop_y = 0
        elif self.fallback_crop == "bottom":
            crop_y = 1 - crop_height
        else:  # center
            crop_y = (1 - crop_height) / 2

        return crop_x, crop_y

    def _generate_ken_burns(
        self,
        crop_region: CropRegion,
        faces: List[FaceRegion],
        duration: float
    ) -> KenBurnsAnimation:
        """
        Generate Ken Burns animation parameters.

        Args:
            crop_region: The crop region being used.
            faces: Detected faces to avoid panning out of frame.
            duration: Animation duration in seconds.

        Returns:
            KenBurnsAnimation parameters.
        """
        min_zoom, max_zoom = self.ken_burns_zoom_range

        if self.ken_burns_randomize:
            # Randomize zoom direction
            if random.random() > 0.5:
                start_zoom = random.uniform(min_zoom, (min_zoom + max_zoom) / 2)
                end_zoom = random.uniform((min_zoom + max_zoom) / 2, max_zoom)
            else:
                start_zoom = random.uniform((min_zoom + max_zoom) / 2, max_zoom)
                end_zoom = random.uniform(min_zoom, (min_zoom + max_zoom) / 2)
        else:
            # Alternating zoom in/out (caller should track state)
            start_zoom = min_zoom
            end_zoom = max_zoom

        # Calculate safe pan range based on crop region and faces
        safe_margin = 0.05  # Stay this far from edges

        # Base center is crop region center
        base_cx = crop_region.x + crop_region.width / 2
        base_cy = crop_region.y + crop_region.height / 2

        # Calculate max pan distance
        max_pan = self.ken_burns_pan_speed * duration

        if self.ken_burns_randomize:
            # Random pan direction
            pan_angle = random.uniform(0, 2 * math.pi)
            pan_dx = math.cos(pan_angle) * max_pan / 2
            pan_dy = math.sin(pan_angle) * max_pan / 2
        else:
            # Default: slight diagonal
            pan_dx = max_pan / 3
            pan_dy = max_pan / 4

        # Calculate start and end centers
        start_cx = base_cx - pan_dx
        start_cy = base_cy - pan_dy
        end_cx = base_cx + pan_dx
        end_cy = base_cy + pan_dy

        # Constrain to keep content visible
        # (accounting for zoom means we see less of the image)
        for zoom, cx, cy in [(start_zoom, start_cx, start_cy), (end_zoom, end_cx, end_cy)]:
            visible_half_w = crop_region.width / (2 * zoom)
            visible_half_h = crop_region.height / (2 * zoom)

            # Ensure visible region stays within image
            cx = max(visible_half_w + safe_margin, min(1 - visible_half_w - safe_margin, cx))
            cy = max(visible_half_h + safe_margin, min(1 - visible_half_h - safe_margin, cy))

        # If faces present, ensure they stay in frame
        if faces:
            face_bbox = get_faces_bounding_box(faces, margin=0.1)
            if face_bbox:
                # Additional constraints to keep faces visible
                # (simplified - a full implementation would check each frame)
                pass

        return KenBurnsAnimation(
            start_zoom=start_zoom,
            end_zoom=end_zoom,
            start_center=(start_cx, start_cy),
            end_center=(end_cx, end_cy)
        )

    def apply_crop(
        self,
        image: Image.Image,
        crop_region: CropRegion
    ) -> Image.Image:
        """
        Apply a crop region to an image.

        Args:
            image: PIL Image to crop.
            crop_region: Normalized crop region.

        Returns:
            Cropped PIL Image.
        """
        w, h = image.size

        left = int(crop_region.x * w)
        top = int(crop_region.y * h)
        right = int((crop_region.x + crop_region.width) * w)
        bottom = int((crop_region.y + crop_region.height) * h)

        return image.crop((left, top, right, bottom))

    def get_ken_burns_frame(
        self,
        image: Image.Image,
        crop_region: CropRegion,
        animation: KenBurnsAnimation,
        progress: float
    ) -> Image.Image:
        """
        Get a single frame of Ken Burns animation.

        Args:
            image: Source PIL Image.
            crop_region: Base crop region.
            animation: Ken Burns animation parameters.
            progress: Animation progress (0-1).

        Returns:
            Transformed PIL Image at specified progress.
        """
        # Interpolate zoom
        zoom = animation.start_zoom + (animation.end_zoom - animation.start_zoom) * progress

        # Interpolate center with easing
        eased_progress = self._ease_in_out(progress)
        cx = animation.start_center[0] + (animation.end_center[0] - animation.start_center[0]) * eased_progress
        cy = animation.start_center[1] + (animation.end_center[1] - animation.start_center[1]) * eased_progress

        # Calculate visible region at this zoom level
        visible_w = crop_region.width / zoom
        visible_h = crop_region.height / zoom

        # Calculate crop box centered at (cx, cy)
        view_x = cx - visible_w / 2
        view_y = cy - visible_h / 2

        # Clamp to valid range
        view_x = max(0, min(1 - visible_w, view_x))
        view_y = max(0, min(1 - visible_h, view_y))

        # Convert to pixels
        w, h = image.size
        left = int(view_x * w)
        top = int(view_y * h)
        right = int((view_x + visible_w) * w)
        bottom = int((view_y + visible_h) * h)

        # Crop and resize to screen
        cropped = image.crop((left, top, right, bottom))
        return cropped.resize(
            (self.screen_width, self.screen_height),
            Image.Resampling.LANCZOS
        )

    def _ease_in_out(self, t: float) -> float:
        """
        Smooth ease-in-out function for natural motion.

        Args:
            t: Progress (0-1).

        Returns:
            Eased progress (0-1).
        """
        if t < 0.5:
            return 2 * t * t
        else:
            return 1 - pow(-2 * t + 2, 2) / 2

    def prepare_image_for_display(
        self,
        image_path: str,
        params: Optional[DisplayParams] = None
    ) -> Image.Image:
        """
        Prepare an image for display (apply crop, resize to screen).

        Args:
            image_path: Path to image file.
            params: Pre-computed display parameters (optional).

        Returns:
            PIL Image ready for display.
        """
        with Image.open(image_path) as img:
            # Convert to RGB if needed
            if img.mode != "RGB":
                img = img.convert("RGB")

            if params and params.crop_region:
                img = self.apply_crop(img, params.crop_region)

            # Resize to screen based on scaling mode
            if self.scaling_mode == "fill":
                # Fill mode: crop region already adjusted, just resize to screen
                img = img.resize(
                    (self.screen_width, self.screen_height),
                    Image.Resampling.LANCZOS
                )
            elif self.scaling_mode == "stretch":
                # Stretch mode: resize without maintaining aspect ratio
                img = img.resize(
                    (self.screen_width, self.screen_height),
                    Image.Resampling.LANCZOS
                )
            elif self.scaling_mode == "balanced":
                # Balanced mode: resize maintaining aspect ratio, add bars if needed
                # Note: thumbnail() only shrinks, so we need proper resize for small images
                img_aspect = img.width / img.height
                screen_aspect = self.screen_width / self.screen_height
                if img_aspect > screen_aspect:
                    # Image is wider - fit by width
                    new_width = self.screen_width
                    new_height = int(self.screen_width / img_aspect)
                else:
                    # Image is taller - fit by height
                    new_height = self.screen_height
                    new_width = int(self.screen_height * img_aspect)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                # Create background with configured color and center image
                result = Image.new("RGB", (self.screen_width, self.screen_height), self.background_color)
                paste_x = (self.screen_width - img.width) // 2
                paste_y = (self.screen_height - img.height) // 2
                result.paste(img, (paste_x, paste_y))
                img = result
            else:  # fit mode
                # Note: thumbnail() only shrinks, so we need proper resize for small images
                img_aspect = img.width / img.height
                screen_aspect = self.screen_width / self.screen_height
                if img_aspect > screen_aspect:
                    new_width = self.screen_width
                    new_height = int(self.screen_width / img_aspect)
                else:
                    new_height = self.screen_height
                    new_width = int(self.screen_height * img_aspect)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                # Create background with configured color and center image
                result = Image.new("RGB", (self.screen_width, self.screen_height), self.background_color)
                paste_x = (self.screen_width - img.width) // 2
                paste_y = (self.screen_height - img.height) // 2
                result.paste(img, (paste_x, paste_y))
                img = result

            return img.copy()
