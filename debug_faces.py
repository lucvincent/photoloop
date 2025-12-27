#!/usr/bin/env python3
# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""Debug script to visualize face detection and crop regions."""

import sys
import os
sys.path.insert(0, '/home/luc/photoloop')

from PIL import Image, ImageDraw, ImageFont
from src.face_detector import FaceDetector, get_faces_bounding_box
from src.image_processor import ImageProcessor

def debug_photo(photo_path: str, output_path: str = None):
    """
    Create a debug image showing face detection and crop region.

    Args:
        photo_path: Path to the photo to analyze
        output_path: Where to save debug image (default: same dir with _debug suffix)
    """
    if not output_path:
        base, ext = os.path.splitext(photo_path)
        output_path = f"{base}_debug{ext}"

    # Load image
    img = Image.open(photo_path)
    img_width, img_height = img.size
    print(f"Image size: {img_width}x{img_height} (aspect: {img_width/img_height:.2f})")

    # Detect faces
    detector = FaceDetector()
    faces = detector.detect_faces(photo_path)
    print(f"Detected {len(faces)} faces:")

    # Create drawing context
    draw = ImageDraw.Draw(img)

    # Draw each face
    for i, face in enumerate(faces):
        # Convert normalized coords to pixels
        x1 = int(face.x * img_width)
        y1 = int(face.y * img_height)
        x2 = int((face.x + face.width) * img_width)
        y2 = int((face.y + face.height) * img_height)

        # Draw face rectangle (green)
        draw.rectangle([x1, y1, x2, y2], outline='lime', width=3)

        # Label
        label = f"F{i+1}: {face.width*100:.1f}%x{face.height*100:.1f}%"
        draw.text((x1, y1 - 20), label, fill='lime')

        conf_str = f", conf={face.confidence:.2f}" if hasattr(face, 'confidence') else ""
        print(f"  Face {i+1}: x={face.x:.3f}, y={face.y:.3f}, w={face.width:.3f}, h={face.height:.3f} ({face.width*100:.1f}%x{face.height*100:.1f}%){conf_str}")

    # Get face bounding box
    if faces:
        bbox = get_faces_bounding_box(faces, margin=0.02)
        if bbox:
            fb_x, fb_y, fb_w, fb_h = bbox
            bx1 = int(fb_x * img_width)
            by1 = int(fb_y * img_height)
            bx2 = int((fb_x + fb_w) * img_width)
            by2 = int((fb_y + fb_h) * img_height)

            # Draw bounding box (yellow dashed)
            draw.rectangle([bx1, by1, bx2, by2], outline='yellow', width=2)
            draw.text((bx1, by1 - 40), f"BBox: {fb_w*100:.1f}%x{fb_h*100:.1f}%", fill='yellow')
            print(f"\nFace bounding box: x={fb_x:.3f}, y={fb_y:.3f}, w={fb_w:.3f}, h={fb_h:.3f}")

    # Compute crop region for 16:9 display
    screen_width, screen_height = 3840, 2160
    processor = ImageProcessor(
        screen_width=screen_width,
        screen_height=screen_height,
        scaling_mode='fill',
        face_position='rule_of_thirds',
        fallback_crop='center'
    )

    screen_aspect = screen_width / screen_height  # 1.78
    img_aspect = img_width / img_height

    # Calculate crop dimensions
    if img_aspect > screen_aspect:
        crop_height = 1.0
        crop_width = screen_aspect / img_aspect
    else:
        crop_width = 1.0
        crop_height = img_aspect / screen_aspect

    print(f"\nCrop needed: {crop_width*100:.1f}% width, {crop_height*100:.1f}% height")

    # Get crop position
    if faces:
        crop_x, crop_y = processor._position_crop_for_faces(crop_width, crop_height, faces)
        print(f"Face-aware crop position: x={crop_x:.3f}, y={crop_y:.3f}")
    else:
        crop_x, crop_y = processor._get_fallback_crop_position(crop_width, crop_height)
        print(f"Fallback crop position: x={crop_x:.3f}, y={crop_y:.3f}")

    # Draw crop region (red)
    cx1 = int(crop_x * img_width)
    cy1 = int(crop_y * img_height)
    cx2 = int((crop_x + crop_width) * img_width)
    cy2 = int((crop_y + crop_height) * img_height)

    draw.rectangle([cx1, cy1, cx2, cy2], outline='red', width=4)
    draw.text((cx1 + 10, cy1 + 10), "CROP REGION", fill='red')

    # Draw what would be cropped (dim overlay)
    # Top strip
    if cy1 > 0:
        overlay = Image.new('RGBA', (img_width, cy1), (0, 0, 0, 128))
        img.paste(overlay, (0, 0), overlay)
    # Bottom strip
    if cy2 < img_height:
        overlay = Image.new('RGBA', (img_width, img_height - cy2), (0, 0, 0, 128))
        img.paste(overlay, (0, cy2), overlay)

    # Save
    img.save(output_path)
    print(f"\nDebug image saved to: {output_path}")

    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_faces.py <photo_path> [output_path]")
        print("\nExample: python debug_faces.py /var/lib/photoloop/cache/abc123.jpg")
        sys.exit(1)

    photo_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    debug_photo(photo_path, output_path)
