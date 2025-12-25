# PhotoLoop

A digital photo frame application for Raspberry Pi that displays photos from public Google Photos albums.

## Features

- **Google Photos Integration**: Displays photos from public Google Photos albums
- **Smart Cropping**: Face-aware cropping keeps subjects in frame
- **Ken Burns Effect**: Subtle zoom and pan animations for a dynamic display
- **Schedule Control**: Automatically turns on/off based on time of day
- **Display Power Control**: Powers off TV/monitor during off-hours (DDC/CI and HDMI-CEC)
- **Web Interface**: Configure and control via browser
- **Offline Support**: Full local caching for reliable playback

## Hardware Requirements

- Raspberry Pi 4 or 5 (4GB+ RAM recommended)
- HDMI display
- Internet connection (for syncing photos)

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/photoloop.git
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
photoloop status    # Show current status
photoloop start     # Force slideshow on
photoloop stop      # Force slideshow off
photoloop resume    # Resume schedule
photoloop sync      # Sync albums now
photoloop reload    # Reload configuration
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
  port: 8080
  host: "0.0.0.0"  # Listen on all interfaces
```

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
  scheduler.py       # Time-based scheduling
  config.py          # Configuration loading
  cli.py             # Command line interface
  web/               # Flask web interface

tests/
  conftest.py        # Pytest fixtures
  test_config.py     # Configuration tests
  test_face_detector.py
  test_image_processor.py
  test_health.py     # System health checks

scripts/
  health_check.py    # Standalone health checker
  config_sync.py     # Config documentation validator
```

### Running Tests

```bash
# Unit tests (use temp directories, safe to run)
pytest tests/test_config.py tests/test_face_detector.py tests/test_image_processor.py -v

# Health checks against live system (read-only, non-disruptive)
pytest tests/test_health.py -v

# All tests
pytest tests/ -v
```

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

MIT License - See LICENSE file for details.
