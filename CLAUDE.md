<!-- Copyright (c) 2025-2026 Luc Vincent. All Rights Reserved. -->
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

### Rendered Frame Cache (Jan 2025)

Pre-renders and caches display frames on disk for instant loading. Replaces the
previous RAM-based preload system.

**How it works:**
1. When a photo is displayed, it's cropped/scaled to screen resolution
2. The rendered frame is saved as high-quality JPEG to disk
3. Next time the same photo is displayed, it loads instantly from cache
4. Cache is invalidated per-photo when resolution or crop window changes

**Cache structure:**
```
/var/lib/photoloop/cache/
├── *.jpg                    # Original downloaded photos
├── metadata.json            # Photo metadata (includes display_params.crop_region)
└── rendered/                # Pre-rendered display frames
    ├── cache_info.json      # Per-photo: resolution, crop_hash, size_bytes
    └── <media_id>.jpg       # Rendered frames (JPEG quality 95)
```

**Cache invalidation (per-photo, lazy):**
- Resolution change: Only re-renders photos as they're displayed at new resolution
- Crop change: Hash of crop_region compared; mismatch triggers re-render
- User can switch back to old resolution and cached frames are still valid

**Config settings:**
```yaml
rendered_cache:
  enabled: true
  max_size_mb: 0       # Max disk space (0 = no limit)
  quality: 95          # JPEG quality (90-98)
```

**Key files:**
- `src/cache_manager.py`: `get_rendered_frame()`, `save_rendered_frame()`, `clear_rendered_cache()`
- `src/main.py`: Main loop uses rendered cache before falling back to processing
- `src/config.py`: `RenderedCacheConfig` dataclass

**Benefits over RAM preloading:**
- Persistent across restarts (no warm-up time)
- No RAM usage for cached frames
- Instant loading for previously-viewed photos
- Works with any number of photos (not limited by RAM)

**Manual cache management:**
```bash
# Check cache size
du -sh /var/lib/photoloop/cache/rendered/

# Clear rendered cache (will rebuild as photos are viewed)
rm -rf /var/lib/photoloop/cache/rendered/
```

### Display Power Control

Controls TV/monitor power during off-hours to save electricity.

**Working Method (Jan 2025): wlopm (Wayland Output Power Management)**

Uses DPMS (Display Power Management Signaling) via `wlopm` tool to put the display
into standby mode. This keeps the Wayland output active while the physical display
sleeps, so SDL2 continues to work properly.

**How it works:**
1. User clicks "Stop" → `wlopm --off HDMI-A-1` puts display in DPMS standby
2. Display enters power saving mode (backlight off, low power)
3. PhotoLoop keeps running (SDL2 window still exists)
4. User clicks "Start" → `wlopm --on HDMI-A-1` wakes display
5. SDL2 renderer is recreated to clear corrupted GPU state (see below)
6. Slideshow resumes at correct resolution

**Critical: DPMS Wake GPU State Corruption (Jan 2025) - FIXED**

After DPMS standby, SDL2's internal GPU state can become corrupted even though the
API reports correct dimensions. This causes the "quarter-screen bug" where photos
render in only the top-left quarter of the display.

**Root cause (discovered Jan 2026):**
The bug is a **race condition with compositor stabilization**. After DPMS wake, the
labwc compositor continues adjusting GPU buffer sizes even after renderer recreation.
The bug was non-deterministic (~50% of the time):
- Sometimes first photo buggy → second fixes it (compositor wasn't ready initially)
- Sometimes first photo OK → second buggy (compositor made deferred adjustments)

**Solution implemented (Jan 2026):**
Extended "GPU burn-in" period after DPMS wake. Render ~45 frames of solid black
(~1.5 seconds) to force the compositor to fully commit buffer sizes before
displaying actual photo content.

**Wake sequence in `_wake_display_if_needed()`:**
1. Turn display on (`wlopm --on`)
2. Brief delay (0.3s) for display to physically wake
3. Recreate renderer (`_refresh_display_dimensions(force_recreate=True)`)
4. GPU burn-in: 45 black frames at 30fps (~1.5s)
5. Then display first photo with normal priming

**Known issue:** Desktop may be visible for several seconds between display wake and
slideshow starting. This is cosmetic. Attempted fix (fullscreen toggle before
renderer recreation) caused the quarter-screen bug to return - needs different approach.

**Previous failed attempts (for reference):**
1. Recreate renderer only - partial fix (first photo works, second breaks)
2. Skip transitions after wake - didn't address root cause
3. Pre-render while display off - vsync blocks, causes long delays

**Note:** Even on fresh service start, SDL2 initially reports 1920x1080 for a 4K
display. The mismatch detection and renderer recreation is needed every time.

**Requirements:**
- `wlopm` package (Wayland output power management)
- `wlr-randr` package (for detecting output name)
- Environment variables: `XDG_RUNTIME_DIR=/run/user/1000`, `WAYLAND_DISPLAY=wayland-0`

**Fallback chain:**
1. wlopm (Wayland DPMS) - primary method for labwc/Wayland
2. HDMI-CEC (for TVs) - uses cec-client
3. Black screen (if neither available)

**Config options:**
- `off_hours_mode: "black"` - display powers off during off-hours
- `off_hours_mode: "clock"` - display stays on showing time/date

**Manual testing:**
```bash
# Check current state
XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 wlopm

# Turn display off (DPMS standby)
XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 wlopm --off HDMI-A-1

# Turn display on
XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 wlopm --on HDMI-A-1
```

### Methods That Did NOT Work (Dec 2024 - Jan 2025)

1. **DDC/CI via ddcutil** - Caused half-resolution bug on Wayland. Display renders
   at quarter size in top-left corner after DDC power cycle. SDL2 reports correct
   dimensions but compositor/GPU uses stale buffer sizes.

2. **wlr-randr --off** - Completely disables Wayland output (not just standby).
   Breaks SDL2 because there's no display to render to. PhotoLoop crashes if it
   tries to start while output is disabled.

3. **vcgencmd display_power** - Doesn't work while labwc (Wayland) is running.
   The compositor has control over the display, not the firmware.

**Files involved:**
- `src/display.py`: `_set_display_power()`, `_get_wayland_output()`, `show_photo()`,
  `_refresh_display_dimensions(force_recreate)`, `_recreate_renderer()`

### Cursor Hiding

On Wayland/labwc, applications cannot hide the cursor directly - the compositor controls it.

**Solution (labwc 0.8.4+):** Use labwc's `HideCursor` + `WarpCursor` actions via keybinding.

**Setup (already configured):**

1. Keybinding in `~/.config/labwc/rc.xml`:
   ```xml
   <keybind key="W-F12">
     <action name="WarpCursor" x="-100" y="-100" />
     <action name="HideCursor" />
   </keybind>
   ```

2. PhotoLoop triggers this on startup via `wtype -M logo -k F12` (in `display.py:_hide_cursor()`)

**Behavior:**
- Cursor is hidden ONLY when PhotoLoop is running (triggers on display init)
- Stays hidden during keyboard/remote input (Fire TV remote works fine)
- Reappears when mouse is physically moved OR when PhotoLoop exits
- Normal desktop cursor works when PhotoLoop is not running

**Manual cursor control:**
```bash
# Hide cursor
wtype -M logo -k F12

# Show cursor - just move the mouse

# After VNC session, restart PhotoLoop to hide cursor again
sudo systemctl restart photoloop
```

**Dependencies:** `wtype` package

**VNC note:** VNC sends mouse position updates which count as "mouse movement" and will
unhide the cursor. This is useful for remote administration. After disconnecting from VNC,
restart PhotoLoop to hide the cursor again.

**Approaches that did NOT work (Dec 2024):**

1. **pygame.mouse.set_visible(False)** - Wayland ignores this; compositor controls cursor
2. **SDL_ShowCursor(SDL_DISABLE)** - Same issue; Wayland compositor overrides
3. **Setting null/transparent cursor via Wayland protocol** - Compositors ignore for security
4. **System-wide invisible cursor theme (XCURSOR_THEME)**:
   - Created `~/.local/share/icons/invisible/` with 1x1 transparent Xcursor via `xcursorgen`
   - Set `XCURSOR_THEME=invisible` in `~/.config/labwc/environment`
   - Problem: Hides cursor system-wide, not just for PhotoLoop
   - Required reboot to take effect
   - Created `cursor-toggle` script to switch between visible/invisible
   - Rejected because we want cursor visible for desktop use
5. **Framebuffer/kmsdrm backend** (SDL_VIDEODRIVER=kmsdrm):
   - Would bypass compositor entirely, no cursor at all
   - Problem: Can't run while labwc is using the display (exclusive access)
   - Would require booting directly into PhotoLoop or VT switching
   - Not pursued because labwc HideCursor worked

**Why labwc HideCursor works:**
- labwc 0.8.4+ added `HideCursor` action (wlroots-based compositor feature)
- Combined with `WarpCursor` to move cursor off-screen (prevents hover effects)
- Triggered via simulated keypress (`wtype`) - works from any process
- Cursor automatically reappears on mouse movement (but NOT keyboard input)
- See: https://github.com/labwc/labwc/discussions/2786

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

### Text Classification (text_classifier.py)

Classifies Google Photos DOM text (captions vs locations vs camera info) using
pattern-based heuristics at display time.

**Architecture: "Dumb Scraper, Smart Display"**

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  album_scraper  │────▶│  cache_manager   │────▶│    display.py   │
│                 │     │                  │     │                 │
│ Extract ALL     │     │ google_raw_texts │     │ Classify text   │
│ text from DOM   │     │ (list of texts)  │     │ using heuristics│
│ without         │     │                  │     │                 │
│ classifying     │     │ google_text_     │◀────│ Cache results   │
│                 │     │ classifications  │     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

1. **Scrape time**: `album_scraper.py` extracts ALL text from Google Photos DOM
   - No classification during scraping (avoids misclassification issues)
   - Raw text stored in `google_raw_texts` field with source context
   - Survives re-scraping - only new text is added

2. **Display time**: `display.py` → `text_classifier.py`
   - When photo is displayed, raw texts are classified
   - Background thread prevents blocking the slideshow
   - Results stored in `google_text_classifications` field

3. **Caching**: Classifications cached to `text_classifications.json`
   - Cache key: MD5 hash of lowercase text
   - Same text always gets same classification (deterministic)
   - Persists across restarts - classification runs once per unique text

**Why Heuristics (Not LLM)**

We tested Ollama with tinyllama model but it performed poorly:
- Classified everything as "location" regardless of content (2/8 correct)
- Slow (~2-3 seconds per classification on Pi 4)
- Required 1GB+ RAM for model

Heuristics are:
- Fast (<1ms per classification)
- Accurate (8/8 correct on test cases)
- Deterministic (same input → same output)
- No dependencies (just regex patterns)

**Classification Types:**
- **camera_info**: Device names (RICOH, Canon, Nikon), settings (ISO, aperture), filenames
- **location**: Country suffixes (", France"), geographic terms (street, plaza), admin regions
- **ui_artifact**: Placeholder text ("Add description", "Details", "Other")
- **date**: Various date formats (Jan 15, 2024; 2024-01-15; Monday)
- **caption**: Everything else (default for user-written descriptions)

**Config options:**
```yaml
text_classifier:
  enabled: true                    # Enable classification at display time
  cache_classifications: true      # Cache results to disk (recommended)
```

Both settings default to True. Disabling is rarely needed since heuristics
are instant and deterministic.

**CLI commands:**
```bash
# Clear cached classifications (triggers re-classification on next display)
photoloop reclassify                    # All photos
photoloop reclassify "Album Name"       # Specific album
photoloop reclassify -y                 # Skip confirmation
```

**Key files:**
- `src/text_classifier.py`: Heuristics-based classifier with detailed architecture docs
- `src/display.py`: `_lazy_classify_if_needed()`, `_apply_classifications()`
- `src/cache_manager.py`: `google_raw_texts`, `google_text_classifications` fields
- `src/album_scraper.py`: `_extract_info_from_detail_view()` raw text extraction

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
photoloop reclassify          # Re-classify all text metadata
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

### Weather and News Ticker (Jan 2026)

The clock display can show weather and scrolling news headlines.

**Weather display:**
- Uses Open-Meteo API (free, no API key required)
- Shows current temperature and conditions (sunny, cloudy, etc.)
- Positioned below the clock, centered

**News ticker:**
- Fetches headlines from configurable RSS feeds
- Continuously scrolling ticker at bottom of screen
- Multiple headlines separated by middle dots (·)
- Headlines automatically truncated if they exceed SDL2's 4096px texture width limit

**Font size options:**
Both weather and news support explicit `font_size` or auto-scaling:
- `font_size: 0` - Auto-scale based on clock size setting (small/medium/large)
- `font_size: 200` - Explicit size in pixels (recommended for large room viewing)

Note: Pygame font sizes render at ~68% of specified height. For example, `font_size: 300`
produces text approximately 200 pixels tall.

**Auto-scale values (font_size: 0):**
| Clock Size | Weather | News   | 4K Height |
|------------|---------|--------|-----------|
| small      | 3.5%    | 3.8%   | ~76/82px  |
| medium     | 4.5%    | 4.8%   | ~97/104px |
| large      | 5.5%    | 5.8%   | ~119/125px|

**Config example:**
```yaml
weather:
  enabled: true
  latitude: 37.445099
  longitude: -122.160362
  units: fahrenheit
  font_size: 225          # Explicit size for room viewing

news:
  enabled: true
  feed_urls:
    - https://feeds.npr.org/1001/rss.xml
    - "# https://feeds.bbci.co.uk/news/rss.xml"  # Commented = disabled
  scroll_speed: 180       # Pixels per second
  max_headlines: 10
  font_size: 175          # Explicit size for room viewing
```

**Key files:**
- `src/clock/renderer.py`: Clock rendering with weather/news overlays
- `src/clock/providers/weather.py`: Open-Meteo weather fetching
- `src/clock/providers/news.py`: RSS headline fetching

## Development Notes

### CRITICAL: Avoid Triggering Unnecessary Syncs

**Syncing is SLOW and resource-intensive.** A full sync of multiple albums can take 30+ minutes.
The scraper uses headless Chrome which is memory-hungry and can OOM on large albums.

**NEVER make changes that could trigger cache invalidation without explicit user approval.**

Cache invalidation happens when metadata.json detects changes to:
- `sync.max_dimension` (resolution settings)
- `scaling.*` settings that affect crop regions

Before modifying any sync/resolution settings:
1. Understand how the change affects cache validation in `cache_manager.py`
2. Test migration logic with a copy of existing metadata
3. WARN the user if there's any risk of cache wipe
4. Get explicit approval before deploying

### Known Issues
- Album scraper can OOM on very large albums (needs batching/streaming)
- Chrome memory usage accumulates during long scroll sessions

### Ken Burns Effect (Experimental)

**Status:** Disabled by default (`ken_burns.enabled: false`). This feature is experimental
with known issues. Enable at your own risk - your mileage may vary.

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

## TODOs

### Display Power Control
- Fix desktop flash during DPMS wake (see TODO in `display.py:_wake_display_if_needed()`)

### Web UI
- Replace Previous/Next button icons (⏮/⏭) with custom SVG single-arrow-with-bar icons
  for a cleaner look. Unicode doesn't have polished single-arrow versions.

### Text Classification
- **Web API classifier**: The caching infrastructure supports expensive classifiers.
  If better accuracy is needed, could add a web API classifier (async, like reverse geocoder)
  that runs in background and updates cache. Current heuristics are 8/8 on test cases though.
- **Additional heuristics**: Add more patterns as edge cases are discovered:
  - More camera manufacturers (Hasselblad, Phase One, etc.)
  - More geographic terms in other languages
  - Better handling of ambiguous single words (e.g., "Lodeve" - city or caption?)
- **Migration improvements**: Current migration from legacy google_caption/google_location
  sets low confidence (0.5) to encourage re-classification. Could improve by analyzing
  the text content to set more accurate confidence scores.
- **Statistics/debugging**: Add web UI panel to view classification stats and
  manually override misclassified texts.
