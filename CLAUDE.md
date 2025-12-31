<!-- Copyright (c) 2025 Luc Vincent. All Rights Reserved. -->
# PhotoLoop

Digital photo frame application for Raspberry Pi that displays photos from public Google Photos albums.

## Project Overview

PhotoLoop is a Python application that:
- Scrapes public Google Photos albums using Selenium/Chrome
- Caches photos locally on the Pi
- Displays them as a fullscreen slideshow with SDL2
- Supports face detection for smart cropping (YuNet DNN model)
- Has Ken Burns effect (slow zoom/pan)
- Web interface for configuration

## Architecture

```
src/
  album_scraper.py   - Selenium-based Google Photos album scraper
  cache_manager.py   - Local photo cache management
  display.py         - SDL2 slideshow renderer with transitions and Ken Burns
  face_detector.py   - YuNet face detection for smart cropping
  image_processor.py - Image scaling, cropping, processing
  main.py            - Application entry point and orchestration
  metadata.py        - Photo metadata extraction (EXIF, IPTC, Google captions)
  scheduler.py       - Time-based schedule (on/off hours)
  config.py          - YAML config loading and validation
  cli.py             - Command line interface
  remote_input.py    - Bluetooth remote control (Fire TV Remote via evdev)
  video_player.py    - Video playback using ffpyplayer
  web/               - Flask web interface (PWA-enabled dashboard)
```

## Key Technical Details

### Hardware Constraints
- Runs on Raspberry Pi with ~4GB RAM
- Memory is tight when running Chrome for scraping
- Display uses SDL2 with hardware acceleration when available

### Album Scraper (album_scraper.py)
- Uses headless Chrome via Selenium
- Google Photos uses virtualized scrolling (only visible items in DOM)
- Captures image URLs from Chrome's performance logs during scrolling
- Memory-intensive for large albums (hundreds/thousands of photos)
- Key challenge: Memory management during long scroll sessions

### Face Detection
- Uses YuNet DNN model (OpenCV)
- Model file: models/face_detection_yunet_2023mar.onnx
- Confidence threshold: 0.6

### Display Power Control
- Controls TV/monitor power during off-hours to save electricity
- Tries multiple methods in order:
  1. **DDC/CI** (for monitors) - uses `ddcutil` package - tried first
  2. **HDMI-CEC** (for TVs) - uses `cec-utils` package
  3. Falls back to black screen if neither available
- When `off_hours_mode: "black"`, display powers off
- When `off_hours_mode: "clock"`, display stays on showing time/date

### Cursor Hiding
- On Raspberry Pi with labwc (Wayland compositor), cursor is controlled by the compositor
- Install script configures `~/.config/labwc/rc.xml` with `cursorHideTimeout=3000`
- Cursor hides automatically after 3 seconds of inactivity
- Allows normal desktop use while keeping cursor hidden during slideshow
- Requires reboot after initial configuration to take effect

### Remote Control (remote_input.py)
- Supports Bluetooth remotes via evdev (e.g., Fire TV Remote)
- Auto-detects remotes on startup
- Auto-reconnects if remote disconnects and reconnects
- Visual feedback overlays for pause/resume/next/previous actions

### Metadata Extraction (metadata.py)
- Extracts EXIF date, GPS coordinates, and embedded captions
- Fetches Google Photos captions via Selenium (optional, slower)
- Caption precedence configurable: google_photos or embedded
- Reverse geocoding for location names from GPS coordinates

### Video Playback (video_player.py)
- Uses ffpyplayer for hardware-accelerated video decode
- Integrates with SDL2 display renderer
- Respects slideshow transitions

## Commands

```bash
# Run the app (development)
python -m src.main

# CLI commands
photoloop status              # Show current status
photoloop start               # Force slideshow on
photoloop stop                # Force slideshow off
photoloop resume              # Resume normal schedule
photoloop next                # Skip to next photo
photoloop sync                # Sync albums (download photos)
photoloop reload              # Reload configuration
photoloop albums              # List configured albums
photoloop add-album URL       # Add a new album
photoloop photos              # List cached photos
photoloop reset-album NAME    # Reset metadata for an album
photoloop update --check      # Check for available updates
photoloop update              # Apply available updates

# Service management
sudo systemctl start/stop/restart/status photoloop

# View logs
sudo journalctl -u photoloop -f
```

## Updating PhotoLoop

PhotoLoop includes a built-in update mechanism:

```bash
# Check what updates are available (safe, no changes made)
photoloop update --check

# Apply updates (updates Python packages, pulls code, restarts service)
photoloop update
```

The update command will:
1. Check for outdated Python packages in the virtual environment
2. Check for PhotoLoop code updates (if installed from git)
3. Apply updates and restart the service

**Note:** System packages (Chromium, SDL2, etc.) are not updated automatically.
Run `sudo apt update && sudo apt upgrade` periodically for system updates.

## Resetting Album Metadata

If metadata (captions, locations) needs to be re-fetched for an album:

```bash
# Reset all metadata (captions + locations)
photoloop reset-album "Album Name"

# Reset only captions (keeps locations)
photoloop reset-album "Album Name" --captions-only

# Reset only locations (keeps captions, GPS coords preserved)
photoloop reset-album "Album Name" --locations-only

# Skip confirmation prompt
photoloop reset-album "Album Name" --yes

# Partial name matching supported
photoloop reset-album "Santa"   # matches "Sante Fe - April 2024"
```

After resetting:
- **Captions**: Re-fetched on next sync (`photoloop sync`)
- **Locations**: Re-geocoded lazily when each photo is displayed (GPS coordinates are preserved)

## Configuration

See `config.yaml` for all settings. Key paths:
- Config: /etc/photoloop/config.yaml (installed) or ./config.yaml (dev)
- Cache: /var/lib/photoloop/cache
- Logs: /var/log/photoloop

### Sync Settings

```yaml
sync:
  interval_minutes: 1440    # How often to sync (1440 = 24 hours, 0 = disabled)
  sync_on_start: false      # Sync immediately when service starts
  sync_time: "03:00"        # Time of day for first scheduled sync (HH:MM format)
```

**Sync behavior:**
- `sync_time`: If set, the first scheduled sync happens at this time, then every `interval_minutes` after
- `sync_on_start`: If true, does an immediate sync on service start (independent of `sync_time`)
- Only enabled albums (marked "Show" in Albums tab) are synced

**Examples:**
- Sync daily at 3am: `sync_time: "03:00"`, `interval_minutes: 1440`
- Sync hourly starting at midnight: `sync_time: "00:00"`, `interval_minutes: 60`
- Sync every 6 hours from service start: `sync_time:` (omit), `interval_minutes: 360`

### Cache Settings

```yaml
cache:
  directory: /var/lib/photoloop/cache
  max_size_mb: 10000    # Maximum cache size (files deleted when exceeded)
```

**Cache behavior:**
- Photos removed from albums are "soft deleted" (excluded from slideshow)
- Actual files remain on disk until cache exceeds `max_size_mb`
- When limit is exceeded, oldest soft-deleted files are removed first
- Local directory photos are referenced in-place (not copied to cache)

### Photo Sources

Albums can be Google Photos URLs or local directories:

```yaml
albums:
  # Google Photos album
  - url: "https://photos.app.goo.gl/..."
    name: "Family Album"
    type: google_photos
    enabled: true

  # Local directory
  - path: "/home/pi/photos"
    name: "Local Photos"
    type: local
    enabled: true
```

Local directories are scanned recursively. EXIF metadata (date, GPS location, captions) is extracted and cached.

### Local Albums Settings

Control the directory browser in the web UI:

```yaml
local_albums:
  enabled: true                              # Allow adding local directories (default: true)
  browse_paths: ["/home", "/media", "/mnt"]  # Paths users can browse
  show_photo_counts: true                    # Show image count per directory (slower)
```

**Security notes:**
- Set `enabled: false` to completely disable local directory support
- `browse_paths` restricts which directories users can browse via the web UI
- Hidden directories (starting with `.`) are never shown
- Only directories within `browse_paths` can be selected

**Note:** Photo counts in the browser show only files in that directory, not subdirectories. However, when a directory is added as an album, all subdirectories are scanned recursively.

### Display Settings

```yaml
display:
  order: random           # Photo order: random, alphabetical, chronological
  photo_duration_seconds: 7
  transition_type: fade   # fade, slide_left, slide_right, slide_up, slide_down, random
```

**Photo order options:**
- `random`: Shuffled order, re-shuffled each time the playlist loops
- `recency_weighted`: Random with recency bias - recent photos appear more often
- `alphabetical`: Sorted by filename (case-insensitive)
- `chronological`: Sorted by date (oldest first)

**Recency-weighted mode settings:**
```yaml
display:
  order: recency_weighted
  recency_cutoff_years: 5.0   # Photos older than this have equal weight
  recency_min_weight: 0.33    # Weight at cutoff (0.33 = 1/3 as likely as new photos)
```

Weight formula: Linear decay from 1.0 (today) to `recency_min_weight` (at cutoff age).
Photos older than cutoff all have `recency_min_weight`. Every photo still appears
once per cycle, but recent photos tend to appear earlier in the shuffled order.

**Date priority** (for chronological and recency_weighted modes):
1. EXIF date (embedded in photo metadata)
2. Google Photos date (scraped from album)
3. File modification time (fallback if no date metadata)

## Development Notes

### Known Issues
- Album scraper can OOM on very large albums (needs batching/streaming)
- Chrome memory usage accumulates during long scroll sessions

### Ken Burns Effect - Disabled (Needs Rework)

**Status:** Disabled in config (`ken_burns.enabled: false`). Needs architectural changes.

**Issues identified (Dec 2024):**

1. **Stretched images during transitions**
   - Problem: When transitioning to a new photo, `_render_transition()` draws `next_texture` at full screen dimensions without applying the Ken Burns viewport
   - Location: `display.py` lines ~606-614 (`_render_fade_transition`, `_render_slide_transition`)
   - The texture has correct aspect ratio, but is stretched to fill screen during fade
   - Result: Jarring stretch-then-correct visual glitch

2. **Panoramas don't work with Ken Burns**
   - Problem: Wide panoramas (e.g., 3:1 after 15% crop → 2.5:1) have Ken Burns zoom into a 16:9 viewport
   - This shows a small vertical slice panning across the panorama - looks wrong
   - Should either: skip Ken Burns for non-16:9 images, or implement horizontal scroll for panoramas

3. **Aliasing artifacts**
   - Problem: Visible jagged edges and shimmer during zoom/pan animation
   - Causes:
     - Using `Image.Resampling.BILINEAR` (fast but lower quality) in `show_photo()`
     - SDL2 default texture scaling lacks proper filtering
   - Fixes needed:
     - Use `LANCZOS` for source texture creation
     - Enable SDL2 texture filtering: `SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "1")` or "2"
     - Consider pre-scaling source texture larger for smoother zoom

**Partial fixes already applied (in codebase but issues remain):**

1. **Aspect ratio preservation in texture creation** (`display.py` ~377-410)
   - Fixed: Source texture now maintains cropped image aspect ratio instead of forcing screen aspect
   - The texture size is calculated to fit within target dimensions while preserving aspect

2. **Aspect ratio preservation in viewport calculation** (`display.py` ~552-577)
   - Fixed: Ken Burns viewport now calculated to match screen aspect ratio
   - Prevents stretching during Ken Burns animation (but not during transitions)

**Files involved:**
- `src/display.py`: `show_photo()`, `_render_slideshow()`, `_render_transition()`, `_get_kb_frame()`
- `src/image_processor.py`: `_generate_ken_burns()`, `get_ken_burns_frame()`
- `src/main.py`: Main loop photo loading

**Recommended approach for proper fix:**
1. Create a separate "Ken Burns ready" texture that's already at screen dimensions with the initial viewport applied
2. During transition, use this pre-rendered texture (no stretching)
3. After transition completes, switch to live Ken Burns rendering
4. Skip Ken Burns entirely for images with aspect ratio >2.0 or <0.5 (extreme panoramas/portraits)
5. Consider horizontal panning mode for panoramas instead of zoom

**Config settings (for reference):**
```yaml
ken_burns:
  enabled: false        # Currently disabled
  zoom_range: [1.0, 1.15]  # 0-15% zoom
  pan_speed: 0.02
  randomize: true
```

### Testing

PhotoLoop has comprehensive tests in the `tests/` directory:

```bash
# Quick health checks (safe, non-disruptive)
photoloop-test --quick

# All health checks with verbose output
photoloop-test --verbose

# Run unit tests
photoloop-test --unit

# Or use pytest directly
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=src --cov-report=term-missing
```

**Test Files:**
- `test_config.py` - Configuration loading/validation
- `test_face_detector.py` - Face detection with YuNet
- `test_image_processor.py` - Image scaling, cropping, smart crop
- `test_scheduler.py` - Schedule evaluation and overrides
- `test_cache_manager.py` - Cache management and playlist navigation
- `test_web_api.py` - Web dashboard API endpoints
- `test_display_control.py` - DDC/CI and HDMI-CEC power control
- `test_health.py` - Live system health validation

### Manual Testing

```bash
# Test scraper
python -c "from src.album_scraper import AlbumScraper; s = AlbumScraper(); print(s.scrape_album('YOUR_URL'))"

# Test face detection on an image
python -c "from src.face_detector import FaceDetector; fd = FaceDetector(); print(fd.detect('/path/to/photo.jpg'))"
```
