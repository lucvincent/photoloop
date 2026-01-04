# Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved.
"""
Text classifier for Google Photos metadata using pattern-based heuristics.

ARCHITECTURE OVERVIEW
=====================
PhotoLoop uses a "dumb scraper, smart display" architecture for Google Photos metadata:

1. SCRAPE TIME (album_scraper.py):
   - Extracts ALL text from Google Photos DOM without classification
   - Stores raw text in `google_raw_texts` field (list of {text, source} dicts)
   - No attempt to distinguish captions from locations during scraping
   - This avoids misclassification issues with accented chars, long names, etc.

2. DISPLAY TIME (display.py → text_classifier.py):
   - When a photo is displayed, raw texts are classified using this module
   - Classification uses pattern-based heuristics (no LLM/ML required)
   - Results stored in `google_text_classifications` field
   - Classified caption/location used for overlay display

3. CACHING:
   - Classifications are cached to disk (text_classifications.json)
   - Cache key is MD5 hash of lowercase text
   - Same text always gets same classification (deterministic)
   - Cache persists across restarts - classification runs once per unique text

WHY HEURISTICS (NOT LLM)
========================
We tried Ollama with tinyllama model but it performed poorly:
- Classified everything as "location" regardless of content
- Slow (~2-3 seconds per classification on Pi 4)
- Required 1GB+ RAM for model

Heuristics are:
- Fast (instant, <1ms per classification)
- Accurate (8/8 correct on test cases vs 2/8 for tinyllama)
- Deterministic (same input → same output)
- No dependencies (just regex patterns)

CLASSIFICATION TYPES
====================
- location: Geographic places (cities, countries, landmarks)
- caption: User descriptions, titles, stories about the photo
- camera_info: Device names, settings (ISO, aperture), filenames
- date: Date/time strings
- ui_artifact: Google Photos UI text ("Add description", "Details")
- unknown: Cannot determine (rare)

CONFIGURATION
=============
Config in config.yaml:
    text_classifier:
      enabled: true              # Enable classification at display time
      cache_classifications: true # Cache results to disk

Both settings default to True. Disabling is rarely needed since heuristics
are instant and deterministic.

FUTURE IMPROVEMENTS
===================
The caching infrastructure supports future use of more expensive classifiers
(e.g., web API calls) without re-scraping. If a better classifier is added:
1. Update classify() method to use new classifier
2. Existing cache can be cleared with `photoloop reclassify`
3. New classifications will be cached automatically

See also:
- cache_manager.py: google_raw_texts, google_text_classifications fields
- display.py: _lazy_classify_if_needed(), _apply_classifications()
- album_scraper.py: _extract_info_from_detail_view() raw text extraction
"""

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """Result of classifying a text string."""
    text: str
    classification: str  # location, caption, date, camera_info, ui_artifact, unknown
    confidence: float  # 0.0 to 1.0
    classified_by: str  # heuristics, migration, manual
    classified_date: str  # ISO format


class TextClassifier:
    """
    Classifies text from Google Photos DOM using heuristics.

    Results are cached to disk to avoid re-running on each display.
    """

    CLASSIFICATIONS = ["location", "caption", "date", "camera_info", "ui_artifact", "unknown"]

    # Minimum confidence to trust a migration classification without re-running
    MIGRATION_CONFIDENCE_THRESHOLD = 0.8

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        cache_classifications: bool = True
    ):
        """
        Initialize the text classifier.

        Args:
            cache_dir: Directory to persist classification cache.
            cache_classifications: Whether to cache results to disk.
        """
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_classifications = cache_classifications

        self._lock = threading.Lock()

        # In-memory cache (also persisted to disk)
        self._cache: Dict[str, ClassificationResult] = {}
        self._cache_dirty = False

        # Load persistent cache
        if self.cache_classifications:
            self._load_cache()

    def _text_hash(self, text: str) -> str:
        """Generate hash for cache key."""
        return hashlib.md5(text.lower().strip().encode()).hexdigest()[:16]

    def _load_cache(self) -> None:
        """Load classification cache from disk."""
        if not self.cache_dir:
            return

        cache_file = self.cache_dir / "text_classifications.json"
        if not cache_file.exists():
            return

        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)

            for key, val in data.items():
                if isinstance(val, dict) and "text" in val:
                    self._cache[key] = ClassificationResult(**val)

            logger.info(f"Loaded {len(self._cache)} cached text classifications")
        except Exception as e:
            logger.warning(f"Failed to load classification cache: {e}")

    def _save_cache(self) -> None:
        """Save classification cache to disk."""
        if not self.cache_dir or not self._cache_dirty or not self.cache_classifications:
            return

        cache_file = self.cache_dir / "text_classifications.json"
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            data = {k: asdict(v) for k, v in self._cache.items()}

            # Atomic write
            tmp_file = cache_file.with_suffix('.tmp')
            with open(tmp_file, 'w') as f:
                json.dump(data, f, indent=2)
            tmp_file.replace(cache_file)

            self._cache_dirty = False
            logger.debug(f"Saved {len(self._cache)} classifications to cache")
        except Exception as e:
            logger.warning(f"Failed to save classification cache: {e}")

    def classify(
        self,
        text: str,
        force_reclassify: bool = False
    ) -> ClassificationResult:
        """
        Classify a single text string using heuristics.

        Args:
            text: Text to classify.
            force_reclassify: If True, ignore cache and re-classify.

        Returns:
            ClassificationResult with classification details.
        """
        text = text.strip()
        if not text:
            return ClassificationResult(
                text=text,
                classification="unknown",
                confidence=0.0,
                classified_by="empty",
                classified_date=datetime.now().isoformat()
            )

        cache_key = self._text_hash(text)

        # Check cache (unless forcing re-classification)
        if not force_reclassify and self.cache_classifications:
            with self._lock:
                if cache_key in self._cache:
                    cached = self._cache[cache_key]
                    # Re-classify if migration with low confidence
                    if (cached.classified_by != "migration" or
                            cached.confidence >= self.MIGRATION_CONFIDENCE_THRESHOLD):
                        return cached

        # Classify using heuristics
        result = self._classify_with_heuristics(text)

        # Cache the result
        if self.cache_classifications:
            with self._lock:
                self._cache[cache_key] = result
                self._cache_dirty = True
            self._save_cache()

        return result

    def classify_batch(
        self,
        texts: List[str],
        force_reclassify: bool = False,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Dict[str, ClassificationResult]:
        """
        Classify multiple texts.

        Args:
            texts: List of texts to classify.
            force_reclassify: If True, ignore cache and re-classify all.
            progress_callback: Optional callback(current, total) for progress.

        Returns:
            Dict mapping text to ClassificationResult.
        """
        results = {}
        total = len(texts)

        for i, text in enumerate(texts):
            results[text] = self.classify(text, force_reclassify=force_reclassify)
            if progress_callback:
                try:
                    progress_callback(i + 1, total)
                except Exception:
                    pass

        return results

    def _classify_with_heuristics(self, text: str) -> ClassificationResult:
        """
        Classify text using pattern-based heuristics.

        Handles:
        - Camera info (device names, settings, filenames)
        - Locations (country suffixes, geographic terms, admin regions)
        - UI artifacts (placeholder text, labels)
        - Dates (various formats)
        - Captions (everything else)
        """
        text_stripped = text.strip()
        text_lower = text_stripped.lower()
        now_iso = datetime.now().isoformat()

        # UI artifacts (highest priority - these are definitely not content)
        ui_patterns = [
            r'^add a description$', r'^add location$', r'^unknown location$',
            r'^details$', r'^other$', r'^info$', r'^map data',
            r'^shared by\b', r'^edited\b'
        ]
        for pattern in ui_patterns:
            if re.match(pattern, text_lower):
                return ClassificationResult(
                    text=text_stripped, classification="ui_artifact",
                    confidence=0.95, classified_by="heuristics",
                    classified_date=now_iso
                )

        # Camera info patterns
        camera_patterns = [
            # Device names
            r'^google pixel', r'^pixel \d', r'^iphone', r'^apple iphone',
            r'^samsung', r'^galaxy', r'^canon', r'^nikon', r'^sony',
            r'^olympus', r'^fujifilm', r'^panasonic', r'^gopro', r'^dji',
            r'^ricoh', r'^leica', r'^pentax',
            # Camera settings
            r'^\d+(\.\d+)?mm$',  # Focal length
            r'^iso\s?\d+', r'^ƒ/', r'^f/',  # ISO and aperture
            r'^\d+/\d+s?$',  # Shutter speed
            r'^\d+(\.\d+)?\s*mp$',  # Megapixels
            r'^\d+\s*[×x]\s*\d+$',  # Dimensions
            r'digital camera$', r'digital photo$',
            # File names
            r'^pxl_\d', r'^img_\d', r'^dsc[_\d]', r'^dcim',
            r'\.(jpe?g|png|heic|gif|webp|mp4|mov)$',
            # Camera manufacturer patterns (with model info)
            r'imaging company',  # RICOH IMAGING COMPANY, LTD.
        ]
        for pattern in camera_patterns:
            if re.search(pattern, text_lower):
                return ClassificationResult(
                    text=text_stripped, classification="camera_info",
                    confidence=0.9, classified_by="heuristics",
                    classified_date=now_iso
                )

        # Date patterns
        date_patterns = [
            # "Jan 15, 2024" or "January 15, 2024"
            r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{4}',
            # "15 Jan 2024"
            r'\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}',
            # "2024-01-15" ISO format
            r'^\d{4}-\d{2}-\d{2}$',
            # Day of week patterns
            r'^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
        ]
        for pattern in date_patterns:
            if re.search(pattern, text_lower):
                return ClassificationResult(
                    text=text_stripped, classification="date",
                    confidence=0.9, classified_by="heuristics",
                    classified_date=now_iso
                )

        # Location patterns - handles Unicode (accents, non-Latin scripts)

        # Common country suffixes (most reliable indicator)
        country_suffixes = [
            ', france', ', italy', ', spain', ', germany', ', japan', ', china',
            ', mexico', ', canada', ', australia', ', uk', ', usa',
            ', united states', ', united kingdom', ', portugal', ', greece',
            ', netherlands', ', belgium', ', austria', ', switzerland',
            ', poland', ', sweden', ', norway', ', denmark', ', ireland',
            ', scotland', ', wales', ', croatia', ', slovenia', ', hungary',
            ', czech republic', ', romania', ', bulgaria', ', turkey',
            ', morocco', ', egypt', ', israel', ', india', ', thailand',
            ', vietnam', ', indonesia', ', philippines', ', malaysia',
            ', singapore', ', south korea', ', new zealand', ', brazil',
            ', argentina', ', chile', ', peru', ', colombia', ', andorra',
        ]
        for suffix in country_suffixes:
            if text_lower.endswith(suffix):
                return ClassificationResult(
                    text=text_stripped, classification="location",
                    confidence=0.95, classified_by="heuristics",
                    classified_date=now_iso
                )

        # US state abbreviations at end
        if re.search(r',\s*[A-Z]{2}$', text_stripped):
            return ClassificationResult(
                text=text_stripped, classification="location",
                confidence=0.85, classified_by="heuristics",
                classified_date=now_iso
            )

        # Administrative region patterns
        admin_patterns = [
            r'\bprovince\s+of\b', r'\bregion\s+of\b', r'\bautonomous\b',
            r'\bdepartment\s+of\b', r'\bdistrict\s+of\b', r'\bcommunity\s+of\b',
            r'\bcounty\b', r'\bmunicipality\b',
        ]
        for pattern in admin_patterns:
            if re.search(pattern, text_lower):
                return ClassificationResult(
                    text=text_stripped, classification="location",
                    confidence=0.85, classified_by="heuristics",
                    classified_date=now_iso
                )

        # Geographic terms (various languages)
        geo_terms = [
            # English
            r'\b(street|avenue|road|boulevard|plaza|square|park|lake|mountain|beach|island|valley|river|bay|harbor|port)\b',
            # French
            r'\b(rue|place|avenue|boulevard|quartier|arrondissement)\b',
            # Spanish
            r'\b(calle|plaza|avenida|barrio)\b',
            # Italian
            r'\b(via|piazza|viale|quartiere)\b',
            # German
            r'\b(strasse|straße|platz|allee)\b',
        ]
        for pattern in geo_terms:
            if re.search(pattern, text_lower):
                return ClassificationResult(
                    text=text_stripped, classification="location",
                    confidence=0.75, classified_by="heuristics",
                    classified_date=now_iso
                )

        # Single known location names (countries, major regions, US states)
        known_locations = {
            'wyoming', 'colorado', 'california', 'new mexico', 'arizona', 'utah',
            'nevada', 'texas', 'florida', 'new york', 'washington', 'oregon',
            'montana', 'idaho', 'alaska', 'hawaii',
            'france', 'italy', 'spain', 'germany', 'japan', 'china', 'india',
            'australia', 'canada', 'mexico', 'brazil', 'argentina',
            'austria', 'andorra', 'portugal', 'ireland', 'scotland', 'wales',
        }
        if text_lower in known_locations:
            return ClassificationResult(
                text=text_stripped, classification="location",
                confidence=0.8, classified_by="heuristics",
                classified_date=now_iso
            )

        # Pattern: "Word, Word" format (often "City, Country" or "Neighborhood, City")
        # Must have comma, allows Unicode letters
        if re.match(r'^[\w\s\-\'\u00C0-\u024F\u0400-\u04FF\u4E00-\u9FFF]+,\s*[\w\s\-\'\u00C0-\u024F\u0400-\u04FF\u4E00-\u9FFF]+$', text_stripped):
            # Short text with comma is likely location
            if len(text_stripped) < 80:
                return ClassificationResult(
                    text=text_stripped, classification="location",
                    confidence=0.6, classified_by="heuristics",
                    classified_date=now_iso
                )

        # Multi-line or very long text is likely caption
        if '\n' in text_stripped or len(text_stripped) > 150:
            return ClassificationResult(
                text=text_stripped, classification="caption",
                confidence=0.7, classified_by="heuristics",
                classified_date=now_iso
            )

        # Default: shorter single-line text without clear pattern -> caption
        # (user-written captions are often short titles)
        return ClassificationResult(
            text=text_stripped, classification="caption",
            confidence=0.4, classified_by="heuristics",
            classified_date=now_iso
        )

    def clear_cache(self, text: Optional[str] = None) -> int:
        """
        Clear classification cache.

        Args:
            text: If provided, clear only this text. Otherwise clear all.

        Returns:
            Number of entries cleared.
        """
        with self._lock:
            if text:
                cache_key = self._text_hash(text)
                if cache_key in self._cache:
                    del self._cache[cache_key]
                    self._cache_dirty = True
                    self._save_cache()
                    return 1
                return 0
            else:
                count = len(self._cache)
                self._cache.clear()
                self._cache_dirty = True
                self._save_cache()
                return count

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get statistics about the classification cache."""
        with self._lock:
            stats = {
                "total_entries": len(self._cache),
                "by_classification": {},
                "by_classifier": {},
            }

            for result in self._cache.values():
                # Count by classification
                cls = result.classification
                stats["by_classification"][cls] = stats["by_classification"].get(cls, 0) + 1

                # Count by classifier
                classifier = result.classified_by
                stats["by_classifier"][classifier] = stats["by_classifier"].get(classifier, 0) + 1

            return stats

    def shutdown(self) -> None:
        """Clean shutdown - save cache."""
        self._save_cache()
