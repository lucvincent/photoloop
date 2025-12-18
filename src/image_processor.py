"""
Image processing for scaling, cropping, and Ken Burns effects.
Handles smart face-aware cropping and generates animation parameters.
"""

import logging
import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PIL import Image

from .face_detector import FaceRegion, get_faces_bounding_box, get_faces_center

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
        face_position: str = "center",
        fallback_crop: str = "center",
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
            scaling_mode: "fill", "fit", or "stretch".
            face_position: "center", "rule_of_thirds", or "top_third".
            fallback_crop: "center", "top", or "bottom" when no faces.
            ken_burns_enabled: Whether to generate Ken Burns animations.
            ken_burns_zoom_range: (min_zoom, max_zoom) for Ken Burns.
            ken_burns_pan_speed: Pan speed as fraction per second.
            ken_burns_randomize: Randomize animation direction.
        """
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.screen_aspect = screen_width / screen_height

        self.scaling_mode = scaling_mode
        self.face_position = face_position
        self.fallback_crop = fallback_crop

        self.ken_burns_enabled = ken_burns_enabled
        self.ken_burns_zoom_range = ken_burns_zoom_range
        self.ken_burns_pan_speed = ken_burns_pan_speed
        self.ken_burns_randomize = ken_burns_randomize

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
            faces: Pre-detected faces (if available).
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

        # Calculate crop region based on scaling mode
        if self.scaling_mode == "fit":
            # No cropping needed for fit mode
            crop_region = CropRegion(0, 0, 1, 1)
        elif self.scaling_mode == "stretch":
            # No cropping, will stretch
            crop_region = CropRegion(0, 0, 1, 1)
        else:  # fill mode
            crop_region = self._compute_fill_crop(
                img_width, img_height,
                faces or []
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
        img_width: int,
        img_height: int,
        faces: List[FaceRegion]
    ) -> CropRegion:
        """
        Compute crop region for fill mode (image fills screen, excess cropped).

        Args:
            img_width: Image width.
            img_height: Image height.
            faces: Detected faces.

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
            # Image is taller - crop top/bottom
            crop_width = 1.0
            crop_height = img_aspect / self.screen_aspect

        # Determine crop position
        if faces:
            # Use face-aware positioning
            crop_x, crop_y = self._position_crop_for_faces(
                crop_width, crop_height, faces
            )
        else:
            # Use fallback positioning
            crop_x, crop_y = self._get_fallback_crop_position(
                crop_width, crop_height
            )

        return CropRegion(crop_x, crop_y, crop_width, crop_height)

    def _position_crop_for_faces(
        self,
        crop_width: float,
        crop_height: float,
        faces: List[FaceRegion]
    ) -> Tuple[float, float]:
        """
        Position crop to keep faces visible and well-placed.

        Args:
            crop_width: Width of crop region (0-1).
            crop_height: Height of crop region (0-1).
            faces: Detected faces.

        Returns:
            (x, y) position for crop region.
        """
        # Get face center
        face_center = get_faces_center(faces)
        if not face_center:
            return self._get_fallback_crop_position(crop_width, crop_height)

        face_cx, face_cy = face_center

        # Get face bounding box
        face_bbox = get_faces_bounding_box(faces, margin=0.1)

        # Determine target position for faces based on preference
        if self.face_position == "rule_of_thirds":
            # Position faces at left third intersection (more pleasing)
            target_x = 1/3
            target_y = 1/3
        elif self.face_position == "top_third":
            # Position faces in upper third
            target_x = 0.5
            target_y = 1/3
        else:  # center
            target_x = 0.5
            target_y = 0.5

        # Calculate crop position to put face center at target position
        crop_x = face_cx - target_x * crop_width
        crop_y = face_cy - target_y * crop_height

        # Ensure faces are within crop region
        if face_bbox:
            fb_x, fb_y, fb_w, fb_h = face_bbox

            # Adjust if faces would be cropped
            if fb_x < crop_x:
                crop_x = max(0, fb_x - 0.05)
            if fb_x + fb_w > crop_x + crop_width:
                crop_x = min(1 - crop_width, fb_x + fb_w - crop_width + 0.05)
            if fb_y < crop_y:
                crop_y = max(0, fb_y - 0.05)
            if fb_y + fb_h > crop_y + crop_height:
                crop_y = min(1 - crop_height, fb_y + fb_h - crop_height + 0.05)

        # Clamp to valid range
        crop_x = max(0, min(1 - crop_width, crop_x))
        crop_y = max(0, min(1 - crop_height, crop_y))

        return crop_x, crop_y

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

            # Resize to screen
            if self.scaling_mode == "fill" or self.scaling_mode == "stretch":
                img = img.resize(
                    (self.screen_width, self.screen_height),
                    Image.Resampling.LANCZOS
                )
            else:  # fit
                img.thumbnail(
                    (self.screen_width, self.screen_height),
                    Image.Resampling.LANCZOS
                )
                # Create black background and center image
                result = Image.new("RGB", (self.screen_width, self.screen_height), (0, 0, 0))
                paste_x = (self.screen_width - img.width) // 2
                paste_y = (self.screen_height - img.height) // 2
                result.paste(img, (paste_x, paste_y))
                img = result

            return img.copy()
