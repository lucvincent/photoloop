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
  album_scraper.py  - Selenium-based Google Photos album scraper
  cache_manager.py  - Local photo cache management
  display.py        - SDL2 slideshow renderer
  face_detector.py  - YuNet face detection for smart cropping
  image_processor.py - Image scaling, cropping, processing
  main.py           - Application entry point
  scheduler.py      - Time-based schedule (on/off hours)
  config.py         - YAML config loading
  cli.py            - Command line interface
  web/              - Flask web interface
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

## Commands

```bash
# Run the app
python -m src.main

# CLI commands
photoloop start/stop/status/sync/reload

# Service
sudo systemctl start/stop/restart/status photoloop
```

## Configuration

See `config.yaml` for all settings. Key paths:
- Config: /etc/photoloop/config.yaml (installed) or ./config.yaml (dev)
- Cache: /var/lib/photoloop/cache
- Logs: /var/log/photoloop

## Development Notes

### Known Issues
- Album scraper can OOM on very large albums (needs batching/streaming)
- Chrome memory usage accumulates during long scroll sessions

### Testing
```bash
# Test scraper
python -c "from src.album_scraper import AlbumScraper; s = AlbumScraper(); print(s.scrape_album('YOUR_URL'))"
```
