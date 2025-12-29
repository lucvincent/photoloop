<!-- Copyright (c) 2025 Luc Vincent. All Rights Reserved. -->
# PhotoLoop

A digital photo frame application for Raspberry Pi that displays photos from public Google Photos albums.

## Features

- **Google Photos Integration**: Displays photos from public Google Photos albums
- **Smart Cropping**: Face-aware cropping keeps subjects in frame
- **Ken Burns Effect**: Subtle zoom and pan animations for a dynamic display
- **Schedule Control**: Automatically turns on/off based on time of day
- **Display Power Control**: Powers off TV/monitor during off-hours (DDC/CI and HDMI-CEC)
- **Web Interface**: Configure and control via browser (installable as PWA on mobile)
- **Remote Control**: Support for Bluetooth remotes (Fire TV Remote) with visual feedback
- **Metadata Overlay**: Display photo date, captions, and location
- **Video Support**: Play videos from your Google Photos albums
- **Offline Support**: Full local caching for reliable playback
- **Self-Updating**: Built-in update mechanism for easy upgrades
- **Network Discovery**: mDNS/Bonjour for easy dashboard access via hostname.local

## Hardware Requirements

- Raspberry Pi 4 or 5 (4GB+ RAM recommended)
- HDMI display
- Internet connection (for syncing photos)

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/lucvincent/photoloop.git
cd photoloop

# Run the installer
sudo bash install.sh
```

### Configuration

1. Edit the configuration file:
   ```bash
   sudo nano /etc/photoloop/config.yaml
   ```

2. Add your Google Photos album URL:
   ```yaml
   albums:
     - url: "https://photos.app.goo.gl/YOUR_ALBUM_URL"
       name: "Family Photos"
   ```

3. Start the service:
   ```bash
   sudo systemctl start photoloop
   ```

## Commands

### CLI Commands

```bash
# Status and control
photoloop status              # Show current status
photoloop start               # Force slideshow on
photoloop stop                # Force slideshow off
photoloop resume              # Resume normal schedule
photoloop next                # Skip to next photo
photoloop prev                # Go to previous photo

# Album management
photoloop sync                # Sync albums (download new photos)
photoloop albums              # List configured albums
photoloop add-album URL       # Add a new album
photoloop photos              # List cached photos

# Configuration
photoloop reload              # Reload configuration

# Updates
photoloop update --check      # Check for available updates
photoloop update              # Apply updates and restart
```

### Test Commands

```bash
photoloop-test              # Run all health checks
photoloop-test --quick      # Quick checks only
photoloop-test --verbose    # Detailed output
photoloop-test --json       # JSON output for automation
photoloop-test --unit       # Run unit tests (may disrupt live system)
```

### Service Commands

```bash
sudo systemctl start photoloop    # Start service
sudo systemctl stop photoloop     # Stop service
sudo systemctl restart photoloop  # Restart service
sudo systemctl status photoloop   # Check service status
sudo journalctl -u photoloop -f   # View live logs
```

## Configuration Reference

### Albums

```yaml
albums:
  - url: "https://photos.app.goo.gl/..."
    name: "Album Name"  # Optional display name
```

### Display Settings

```yaml
display:
  resolution: "auto"           # "auto" or "1920x1080"
  photo_duration_seconds: 30   # How long each photo displays
  video_enabled: false         # Enable video playback
  transition_type: "fade"      # fade, slide, none
  transition_duration_ms: 1000
  order: "random"              # random, sequential, date
```

### Scaling Modes

```yaml
scaling:
  mode: "fill"              # fill, fit, balanced, stretch
  max_crop_percent: 15      # Max crop for balanced mode (0-50)
  background_color: [0,0,0] # For fit mode letterboxing
  face_detection: true      # Enable smart face-aware cropping
  face_position: "center"   # center, rule_of_thirds
  fallback_crop: "center"   # For images without faces
```

**Scaling Mode Comparison:**

| Mode | Behavior | Use Case |
|------|----------|----------|
| `fill` | Crops to fill screen, no black bars | Best for most photos |
| `fit` | Shows entire image with letterboxing | Preserve full image |
| `balanced` | Limited crop (up to max_crop_percent) | Compromise approach |
| `stretch` | Stretches to fill (distorts image) | Not recommended |

### Ken Burns Effect

```yaml
ken_burns:
  enabled: true
  zoom_range: [1.0, 1.15]  # Min/max zoom levels
  pan_speed: 0.02          # Pan movement speed
  randomize: true          # Randomize effect direction
```

### Schedule

```yaml
schedule:
  enabled: true
  off_hours_mode: "black"  # black, clock
  weekday:
    start_time: "07:00"
    end_time: "22:00"
  weekend:
    start_time: "08:00"
    end_time: "23:00"
```

### Metadata Overlay

```yaml
overlay:
  enabled: true
  show_date: true
  show_caption: true
  date_format: "%B %d, %Y"      # e.g., "December 25, 2024"
  font_size: 36                  # Font size in pixels
  font_color: [255, 255, 255]    # White text
  background_color: [0, 0, 0, 128]  # Semi-transparent black
  position: "bottom_left"        # bottom_left, bottom_right, top_left, top_right
  padding: 20
  max_caption_length: 200        # Truncate long captions
```

### Remote Control

PhotoLoop supports Bluetooth remotes like the Amazon Fire TV Remote:

- **Left/Right**: Previous/Next photo
- **Center/Select**: Toggle pause

Remotes are auto-detected on startup. No configuration needed.

### Cache

```yaml
cache:
  directory: "/var/lib/photoloop/cache"
  max_size_mb: 1000  # Maximum cache size
```

### Web Interface

```yaml
web:
  enabled: true
  port: 8080        # Dashboard port (default: 8080)
  host: "0.0.0.0"   # Listen on all interfaces
```

**Note:** If you change the port, also update `/etc/avahi/services/photoloop.service` to match, so network discovery advertises the correct port.

## Accessing the Web Dashboard

### Local Network Discovery (mDNS)

PhotoLoop registers itself for local network discovery using mDNS/Bonjour. Access the dashboard at:

```
http://<hostname>.local:8080
```

For example, if your Raspberry Pi's hostname is `photopi1`:
```
http://photopi1.local:8080
```

This works on:
- macOS and iOS (built-in)
- Windows 10/11 (built-in)
- Linux (with avahi-daemon installed)
- Android (varies by device/app)

### Multiple PhotoLoop Instances

If you have multiple PhotoLoop instances on your network (e.g., `photopi1`, `photopi2`), each is accessible by its hostname:

```
http://photopi1.local:8080
http://photopi2.local:8080
```

Each instance also advertises itself as a service (e.g., "PhotoLoop (photopi1)") for apps that support Bonjour/mDNS service browsing.

### Direct IP Access

You can also access the dashboard using the device's IP address:

```
http://<ip-address>:8080
```

Find the IP with: `hostname -I` on the Pi.

### Installing as a Mobile App (PWA)

The dashboard can be installed as a Progressive Web App on your phone for quick access:

**Android (Chrome):**
1. Open the dashboard URL in Chrome
2. Tap the menu (⋮) → "Add to Home screen" or "Install app"
3. Tap "Install"

**iOS (Safari):**
1. Open the dashboard URL in Safari
2. Tap the Share button → "Add to Home Screen"
3. Tap "Add"

Once installed, the dashboard opens in standalone mode (no browser chrome) and feels like a native app.

## Directory Structure

```
/opt/photoloop/           # Application files
/etc/photoloop/           # Configuration
  config.yaml
/var/lib/photoloop/       # Data
  cache/                  # Cached photos
    metadata.json         # Photo metadata
/var/log/photoloop/       # Logs
```

## Updating PhotoLoop

### Using the Built-in Updater

```bash
# Check for available updates (safe, no changes made)
photoloop update --check

# Apply updates
photoloop update
```

The update command will:
1. Update Python packages in the virtual environment
2. Pull latest code changes (if installed from git)
3. Restart the PhotoLoop service

### System Updates

System packages (Chromium, SDL2, etc.) are not updated automatically. Run periodically:

```bash
sudo apt update && sudo apt upgrade
```

## Troubleshooting

### Check System Health

```bash
photoloop-test --verbose
```

### View Logs

```bash
# Recent logs
sudo journalctl -u photoloop -n 50

# Follow live logs
sudo journalctl -u photoloop -f

# Errors only
sudo journalctl -u photoloop -p err
```

### Common Issues

**Photos not displaying:**
1. Check if service is running: `systemctl status photoloop`
2. Verify album URL in config
3. Run manual sync: `photoloop sync`
4. Check logs for errors

**Display at wrong resolution:**
- Service automatically detects resolution on startup
- Restart service after display changes: `sudo systemctl restart photoloop`

**All photos were deleted:**
- The sync process has a 50% retention threshold
- If scraper fails, existing photos are preserved
- Check logs for scraper errors

**Face detection not working:**
- Ensure model file exists: `/opt/photoloop/models/face_detection_yunet_2023mar.onnx`
- Check `face_detection: true` in config

## Development

### Project Structure

```
src/
  album_scraper.py   # Selenium-based Google Photos scraper
  cache_manager.py   # Local photo cache management
  display.py         # SDL2/pygame slideshow renderer
  face_detector.py   # YuNet face detection
  image_processor.py # Image scaling and cropping
  main.py            # Application entry point
  metadata.py        # Photo metadata extraction (EXIF, IPTC, captions)
  scheduler.py       # Time-based scheduling
  config.py          # Configuration loading
  cli.py             # Command line interface
  remote_input.py    # Bluetooth remote control (evdev)
  video_player.py    # Video playback (ffpyplayer)
  web/               # Flask web interface (PWA-enabled)

tests/
  conftest.py             # Pytest fixtures and test utilities
  test_cache_manager.py   # Cache and playlist tests
  test_config.py          # Configuration loading tests
  test_display_control.py # DDC/CI and HDMI-CEC power control tests
  test_face_detector.py   # Face detection tests
  test_health.py          # System health checks
  test_image_processor.py # Image processing tests
  test_scheduler.py       # Schedule evaluation tests
  test_web_api.py         # Web dashboard API tests

scripts/
  health_check.py    # Standalone health checker
  config_sync.py     # Config documentation validator
  photoloop-test     # Unified test runner CLI
```

### Running Tests

PhotoLoop has two ways to run tests:

#### Using photoloop-test (Recommended)

The `photoloop-test` command provides a unified interface for all testing:

```bash
# Run all health checks (safe, non-disruptive)
photoloop-test

# Quick health checks only
photoloop-test --quick

# Verbose output with detailed results
photoloop-test --verbose

# JSON output for automation/scripting
photoloop-test --json

# Run unit tests (may briefly disrupt live system)
photoloop-test --unit

# Run specific test file
photoloop-test --unit -k test_config
```

#### Using pytest directly

For development, you can run pytest directly:

```bash
# All unit tests (use temp directories, safe to run anytime)
pytest tests/test_config.py tests/test_face_detector.py tests/test_image_processor.py tests/test_scheduler.py tests/test_cache_manager.py -v

# Web API tests (uses test fixtures, safe to run)
pytest tests/test_web_api.py -v

# Display control tests (checks DDC/CI and HDMI-CEC)
pytest tests/test_display_control.py -v

# Health checks against live system (read-only, non-disruptive)
pytest tests/test_health.py -v

# All tests
pytest tests/ -v

# Run with coverage report
pytest tests/ -v --cov=src --cov-report=term-missing
```

#### Test Categories

| Test File | Type | Safe to Run | Description |
|-----------|------|-------------|-------------|
| `test_config.py` | Unit | Yes | Configuration loading and validation |
| `test_face_detector.py` | Unit | Yes | Face detection with YuNet model |
| `test_image_processor.py` | Unit | Yes | Image scaling, cropping, smart crop |
| `test_scheduler.py` | Unit | Yes | Schedule evaluation and overrides |
| `test_cache_manager.py` | Unit | Yes | Cache management and playlist |
| `test_web_api.py` | Unit | Yes | Web dashboard API endpoints |
| `test_display_control.py` | Integration | Yes | DDC/CI and HDMI-CEC power control |
| `test_health.py` | System | Yes | Live system health validation |

### Running from Source

```bash
# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run directly
python -m src.main

# With custom config
python -m src.main --config ./config.yaml
```

## License

Copyright (c) 2025 Luc Vincent. All Rights Reserved.

See LICENSE file for details.
