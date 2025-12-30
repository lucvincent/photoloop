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
