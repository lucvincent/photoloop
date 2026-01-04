# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""
Aesthetic image cropping using GAIC (Grid Anchor based Image Cropping).

GAIC is trained to predict aesthetically pleasing crop regions by learning
from professional photography compositions. It uses a neural network to
score different crop candidates and find the optimal one.

This module adapts GAIC to work on CPU without CUDA by using torchvision
operations instead of custom CUDA kernels.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Try to import torch - it's optional
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torchvision.ops import roi_align
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("PyTorch not available. Aesthetic cropping will use fallback method.")


@dataclass
class CropCandidate:
    """Represents a potential crop region with its aesthetic score."""
    x: float      # Left edge (normalized 0-1)
    y: float      # Top edge (normalized 0-1)
    width: float  # Width (normalized 0-1)
    height: float # Height (normalized 0-1)
    score: float  # Aesthetic score (higher is better)

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "score": self.score
        }


# PyTorch-dependent classes - only defined when torch is available
if TORCH_AVAILABLE:
    class RoIAlignCPU(nn.Module):
        """CPU-compatible RoI Align using torchvision."""

        def __init__(self, output_size: int, spatial_scale: float):
            super().__init__()
            self.output_size = output_size
            self.spatial_scale = spatial_scale

        def forward(self, features: 'torch.Tensor', boxes: 'torch.Tensor') -> 'torch.Tensor':
            """
            Apply RoI Align to extract features from regions.

            Args:
                features: (N, C, H, W) feature tensor
                boxes: (K, 5) tensor with [batch_idx, x1, y1, x2, y2]

            Returns:
                (K, C, output_size, output_size) aligned features
            """
            # torchvision roi_align expects boxes in (x1, y1, x2, y2) format
            # and a list of tensors, one per batch item
            batch_size = features.shape[0]
            box_list = []

            for b in range(batch_size):
                mask = boxes[:, 0] == b
                box_list.append(boxes[mask, 1:5] * self.spatial_scale)

            return roi_align(
                features,
                box_list,
                output_size=self.output_size,
                spatial_scale=1.0,  # Already scaled boxes
                aligned=True
            )

    class RoDAlignCPU(nn.Module):
        """
        CPU-compatible RoD (Region of Discarding) Align.

        RoDAlign extracts features from the region OUTSIDE the crop box,
        which helps the model learn what should be excluded. This is a
        simplified CPU implementation that approximates the CUDA version.
        """

        def __init__(self, output_size: int, spatial_scale: float):
            super().__init__()
            self.output_size = output_size
            self.spatial_scale = spatial_scale

        def forward(self, features: 'torch.Tensor', boxes: 'torch.Tensor') -> 'torch.Tensor':
            """
            Extract features from regions outside the boxes.

            This creates a "context" representation by pooling features
            from the entire image with the box region masked out.
            """
            batch_size, channels, height, width = features.shape
            num_boxes = boxes.shape[0]

            # Output tensor
            output = torch.zeros(
                num_boxes, channels, self.output_size, self.output_size,
                device=features.device, dtype=features.dtype
            )

            for i in range(num_boxes):
                batch_idx = int(boxes[i, 0])
                x1 = int(boxes[i, 1] * self.spatial_scale)
                y1 = int(boxes[i, 2] * self.spatial_scale)
                x2 = int(boxes[i, 3] * self.spatial_scale)
                y2 = int(boxes[i, 4] * self.spatial_scale)

                # Clamp to feature map bounds
                x1 = max(0, min(x1, width))
                y1 = max(0, min(y1, height))
                x2 = max(0, min(x2, width))
                y2 = max(0, min(y2, height))

                # Create mask for outside region
                feat = features[batch_idx].clone()
                mask = torch.ones(height, width, device=features.device)
                if x2 > x1 and y2 > y1:
                    mask[y1:y2, x1:x2] = 0

                # Apply mask and pool
                masked_feat = feat * mask.unsqueeze(0)

                # Adaptive average pool to output size
                pooled = F.adaptive_avg_pool2d(masked_feat.unsqueeze(0), self.output_size)
                output[i] = pooled[0]

            return output

    def _build_fc_layers(reddim: int = 32, alignsize: int = 8) -> 'nn.Sequential':
        """Build the fully connected layers for score prediction."""
        return nn.Sequential(
            nn.Conv2d(reddim, 768, kernel_size=alignsize, padding=0),
            nn.ReLU(inplace=True),
            nn.Conv2d(768, 128, kernel_size=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(128, 1, kernel_size=1)
        )

    class GAICModel(nn.Module):
        """
        GAIC model adapted for CPU inference.

        Uses MobileNetV2 backbone with CPU-compatible RoI operations.
        """

        def __init__(self, alignsize: int = 8, reddim: int = 32):
            super().__init__()

            # Use MobileNetV2 as backbone (lightweight)
            from torchvision.models import mobilenet_v2, MobileNet_V2_Weights

            model = mobilenet_v2(weights=MobileNet_V2_Weights.DEFAULT)

            # Extract feature layers at different scales
            self.feature3 = nn.Sequential(*list(model.features[:7]))   # 1/8 scale
            self.feature4 = nn.Sequential(*list(model.features[7:14])) # 1/16 scale
            self.feature5 = nn.Sequential(*list(model.features[14:-1])) # 1/32 scale

            # Dimension reduction (448 = 32 + 96 + 320 channels from MobileNetV2)
            self.DimRed = nn.Conv2d(448, reddim, kernel_size=1, padding=0)

            # RoI operations (CPU versions)
            downsample = 4
            self.RoIAlign = RoIAlignCPU(alignsize, 1.0 / 2**downsample)
            self.RoDAlign = RoDAlignCPU(alignsize, 1.0 / 2**downsample)

            # Prediction layers
            self.FC_layers = _build_fc_layers(reddim * 2, alignsize)

        def forward(self, images: 'torch.Tensor', boxes: 'torch.Tensor') -> 'torch.Tensor':
            """
            Score crop candidates.

            Args:
                images: (N, 3, H, W) input images
                boxes: (N, K, 4) crop boxes in [x1, y1, x2, y2] format (pixel coords)

            Returns:
                (N*K,) aesthetic scores for each crop
            """
            B, N, _ = boxes.shape

            # Add batch indices to boxes
            if boxes.shape[-1] == 4:
                index = torch.arange(B, device=boxes.device).view(-1, 1).repeat(1, N).reshape(B, N, 1)
                boxes = torch.cat((index, boxes), dim=-1).contiguous()

            if boxes.dim() == 3:
                boxes = boxes.view(-1, 5)

            # Extract multi-scale features
            f3 = self.feature3(images)
            f4 = self.feature4(f3)
            f5 = self.feature5(f4)

            # Upsample to common resolution
            f3 = F.interpolate(f3, size=f4.shape[2:], mode='bilinear', align_corners=True)
            f5 = F.interpolate(f5, size=f4.shape[2:], mode='bilinear', align_corners=True)

            # Concatenate features
            cat_feat = torch.cat((f3, f4, 0.5 * f5), dim=1)

            # Reduce dimensions
            red_feat = self.DimRed(cat_feat)

            # Extract RoI and RoD features
            RoI_feat = self.RoIAlign(red_feat, boxes)
            RoD_feat = self.RoDAlign(red_feat, boxes)

            # Combine and predict
            final_feat = torch.cat((RoI_feat, RoD_feat), dim=1)
            prediction = self.FC_layers(final_feat)

            return prediction.view(-1)


class AestheticCropper:
    """
    Find aesthetically pleasing crops using GAIC or fallback methods.

    This class provides the main interface for aesthetic cropping.
    If GAIC/PyTorch is not available, it falls back to a rule-based
    approach using saliency and composition guidelines.
    """

    # Model paths
    DEFAULT_MODEL_PATH = "/opt/photoloop/models/gaic_mobilenetv2.pth"
    DEV_MODEL_PATH = "models/gaic_mobilenetv2.pth"

    def __init__(
        self,
        model_path: Optional[str] = None,
        use_gpu: bool = False
    ):
        """
        Initialize the aesthetic cropper.

        Args:
            model_path: Path to GAIC model weights (optional).
            use_gpu: Whether to use GPU if available (default False for Pi).
        """
        self.model = None
        self.device = torch.device("cpu") if TORCH_AVAILABLE else None

        if use_gpu and TORCH_AVAILABLE and torch.cuda.is_available():
            self.device = torch.device("cuda")

        # Find model file
        if model_path:
            self.model_path = model_path
        elif os.path.exists(self.DEFAULT_MODEL_PATH):
            self.model_path = self.DEFAULT_MODEL_PATH
        elif os.path.exists(self.DEV_MODEL_PATH):
            self.model_path = self.DEV_MODEL_PATH
        else:
            src_dir = Path(__file__).parent
            model_file = src_dir.parent / "models" / "gaic_mobilenetv2.pth"
            self.model_path = str(model_file) if model_file.exists() else None

        if self.model_path and os.path.exists(self.model_path):
            self._load_model()
        else:
            logger.info(
                "GAIC model not found. Using composition-based fallback. "
                "For best results, download the model to: %s",
                self.DEFAULT_MODEL_PATH
            )

    def _load_model(self) -> bool:
        """Load the GAIC model."""
        if not TORCH_AVAILABLE:
            return False

        if self.model is not None:
            return True

        if not self.model_path or not os.path.exists(self.model_path):
            return False

        try:
            self.model = GAICModel()
            state_dict = torch.load(self.model_path, map_location=self.device)
            self.model.load_state_dict(state_dict)
            self.model.to(self.device)
            self.model.eval()
            logger.info(f"Loaded GAIC model from {self.model_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load GAIC model: {e}")
            self.model = None
            return False

    def _generate_crop_candidates(
        self,
        image_width: int,
        image_height: int,
        target_ratio: float,
        num_candidates: int = 64,
        min_crop_ratio: float = 0.5
    ) -> List[Tuple[int, int, int, int]]:
        """
        Generate diverse crop candidates for scoring.

        Args:
            image_width: Original image width
            image_height: Original image height
            target_ratio: Desired width/height ratio
            num_candidates: Number of candidates to generate
            min_crop_ratio: Minimum crop size as fraction of image

        Returns:
            List of (x1, y1, x2, y2) crop boxes in pixel coordinates
        """
        candidates = []
        image_ratio = image_width / image_height

        # Generate crops at different scales
        for scale in np.linspace(min_crop_ratio, 1.0, 4):
            if target_ratio > image_ratio:
                # Width-constrained
                crop_w = int(image_width * scale)
                crop_h = int(crop_w / target_ratio)
            else:
                # Height-constrained
                crop_h = int(image_height * scale)
                crop_w = int(crop_h * target_ratio)

            if crop_w > image_width or crop_h > image_height:
                continue

            # Generate positions using grid + rule-of-thirds points
            x_positions = np.linspace(0, image_width - crop_w, 4).astype(int)
            y_positions = np.linspace(0, image_height - crop_h, 4).astype(int)

            for x in x_positions:
                for y in y_positions:
                    candidates.append((x, y, x + crop_w, y + crop_h))

        # Add rule-of-thirds centered crops
        for scale in [0.7, 0.8, 0.9, 1.0]:
            if target_ratio > image_ratio:
                crop_w = int(image_width * scale)
                crop_h = int(crop_w / target_ratio)
            else:
                crop_h = int(image_height * scale)
                crop_w = int(crop_h * target_ratio)

            if crop_w <= image_width and crop_h <= image_height:
                # Center crop
                x = (image_width - crop_w) // 2
                y = (image_height - crop_h) // 2
                candidates.append((x, y, x + crop_w, y + crop_h))

                # Rule of thirds offsets
                for x_off in [-crop_w // 6, crop_w // 6]:
                    for y_off in [-crop_h // 6, crop_h // 6]:
                        nx = max(0, min(image_width - crop_w, x + x_off))
                        ny = max(0, min(image_height - crop_h, y + y_off))
                        candidates.append((nx, ny, nx + crop_w, ny + crop_h))

        # Remove duplicates and limit
        unique = list(set(candidates))
        return unique[:num_candidates]

    def _score_crops_gaic(
        self,
        image: np.ndarray,
        candidates: List[Tuple[int, int, int, int]]
    ) -> List[float]:
        """Score crops using GAIC model."""
        if self.model is None:
            return [0.0] * len(candidates)

        height, width = image.shape[:2]

        # Prepare image tensor
        img_resized = cv2.resize(image, (224, 224))
        img_rgb = cv2.cvtColor(img_resized, cv2.COLOR_BGR2RGB)
        img_normalized = img_rgb.astype(np.float32) / 255.0

        # ImageNet normalization
        mean = np.array([0.485, 0.456, 0.406])
        std = np.array([0.229, 0.224, 0.225])
        img_normalized = (img_normalized - mean) / std

        # Convert to tensor (NCHW)
        img_tensor = torch.from_numpy(img_normalized).permute(2, 0, 1).unsqueeze(0).float()
        img_tensor = img_tensor.to(self.device)

        # Scale boxes to resized image coordinates
        scale_x = 224.0 / width
        scale_y = 224.0 / height

        boxes_list = []
        for (x1, y1, x2, y2) in candidates:
            boxes_list.append([
                x1 * scale_x,
                y1 * scale_y,
                x2 * scale_x,
                y2 * scale_y
            ])

        boxes_tensor = torch.tensor([boxes_list], dtype=torch.float32, device=self.device)

        # Run inference
        with torch.no_grad():
            scores = self.model(img_tensor, boxes_tensor)

        return scores.cpu().numpy().tolist()

    def _score_crops_composition(
        self,
        image: np.ndarray,
        candidates: List[Tuple[int, int, int, int]],
        saliency_map: Optional[np.ndarray] = None
    ) -> List[float]:
        """
        Score crops using composition rules and saliency.

        This is the fallback when GAIC is not available.
        """
        height, width = image.shape[:2]
        scores = []

        for (x1, y1, x2, y2) in candidates:
            crop_w = x2 - x1
            crop_h = y2 - y1
            score = 0.0

            # 1. Prefer larger crops (less cropping)
            size_score = (crop_w * crop_h) / (width * height)
            score += size_score * 0.3

            # 2. Prefer centered crops
            center_x = (x1 + x2) / 2 / width
            center_y = (y1 + y2) / 2 / height
            center_dist = ((center_x - 0.5)**2 + (center_y - 0.5)**2) ** 0.5
            center_score = 1.0 - min(1.0, center_dist * 2)
            score += center_score * 0.2

            # 3. Saliency coverage
            if saliency_map is not None:
                crop_saliency = saliency_map[y1:y2, x1:x2]
                total_saliency = np.sum(saliency_map)
                if total_saliency > 0:
                    coverage = np.sum(crop_saliency) / total_saliency
                    score += coverage * 0.5

            scores.append(score)

        return scores

    def find_best_crop(
        self,
        image_path: str,
        target_ratio: float,
        saliency_map: Optional[np.ndarray] = None,
        num_candidates: int = 64
    ) -> Optional[CropCandidate]:
        """
        Find the best aesthetic crop for an image.

        Args:
            image_path: Path to the image file.
            target_ratio: Desired width/height ratio for the crop.
            saliency_map: Optional pre-computed saliency map.
            num_candidates: Number of crop candidates to evaluate.

        Returns:
            Best CropCandidate, or None if processing fails.
        """
        try:
            image = cv2.imread(image_path)
            if image is None:
                logger.error(f"Failed to load image: {image_path}")
                return None

            height, width = image.shape[:2]

            # Generate candidates
            candidates = self._generate_crop_candidates(
                width, height, target_ratio, num_candidates
            )

            if not candidates:
                return None

            # Score candidates
            if self.model is not None:
                scores = self._score_crops_gaic(image, candidates)
            else:
                scores = self._score_crops_composition(image, candidates, saliency_map)

            # Find best
            best_idx = np.argmax(scores)
            x1, y1, x2, y2 = candidates[best_idx]

            return CropCandidate(
                x=x1 / width,
                y=y1 / height,
                width=(x2 - x1) / width,
                height=(y2 - y1) / height,
                score=float(scores[best_idx])
            )

        except Exception as e:
            logger.error(f"Aesthetic cropping failed: {e}")
            return None

    def get_ranked_crops(
        self,
        image_path: str,
        target_ratio: float,
        top_k: int = 5,
        saliency_map: Optional[np.ndarray] = None
    ) -> List[CropCandidate]:
        """
        Get top-k ranked crop candidates.

        Args:
            image_path: Path to the image file.
            target_ratio: Desired width/height ratio.
            top_k: Number of top candidates to return.
            saliency_map: Optional pre-computed saliency map.

        Returns:
            List of CropCandidate objects, sorted by score.
        """
        try:
            image = cv2.imread(image_path)
            if image is None:
                return []

            height, width = image.shape[:2]

            candidates = self._generate_crop_candidates(
                width, height, target_ratio, num_candidates=100
            )

            if not candidates:
                return []

            if self.model is not None:
                scores = self._score_crops_gaic(image, candidates)
            else:
                scores = self._score_crops_composition(image, candidates, saliency_map)

            # Create crop candidates with scores
            results = []
            for (x1, y1, x2, y2), score in zip(candidates, scores):
                results.append(CropCandidate(
                    x=x1 / width,
                    y=y1 / height,
                    width=(x2 - x1) / width,
                    height=(y2 - y1) / height,
                    score=float(score)
                ))

            # Sort by score and return top k
            results.sort(key=lambda c: c.score, reverse=True)
            return results[:top_k]

        except Exception as e:
            logger.error(f"Ranked crop generation failed: {e}")
            return []
