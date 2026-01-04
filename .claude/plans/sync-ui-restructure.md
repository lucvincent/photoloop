# Sync UI Restructure Plan

## Status: IMPLEMENTED (Jan 3, 2026)

Code changes complete. Waiting for current sync to finish before deploying/restarting service.

## Current State (Jan 3, 2026)
- Sync is currently in progress (triggered by cache wipe from max_dimension migration bug)
- Code changes can be made but should NOT restart service until sync completes
- Check sync status: `curl -s http://localhost:8080/api/sync/status | python3 -m json.tool`

## Task: Restructure Album Sync in Control Tab

### Current Structure (Control tab)
1. Photo Control (play/pause, prev/next)
2. Slideshow Control (start/stop/resume)
3. Sync section (button + progress, not a proper section)

### New Structure (Control tab)
1. **Photo Control** (unchanged)
2. **Slideshow Control** (unchanged)
3. **Album Sync** (NEW top-level section with h2 header)

### Album Sync Section Requirements

#### Settings to expose:
- `sync.enabled` (NEW) - "Enable Automatic Syncs" checkbox
- `sync.interval_minutes` - dropdown or input (1440 = daily, etc.)
- `sync.sync_time` - time picker (HH:MM format)
- `sync.sync_on_start` - checkbox "Sync on service start"
- `sync.max_dimension` - input (0 = full resolution, or pixels like 1920)

#### Buttons:
- **SYNC NOW** button (primary)
- **STOP SYNC** button (red/danger, only visible when sync in progress)

#### Below SYNC NOW:
- Checkbox: "Also update Google Photos metadata"
  - (renamed from "Update all metadata...")
  - Keep existing help text underneath

#### Progress display (when syncing):
- Progress bar (as today)
- Stage text (scraping/downloading)
- Details text (album name, counts)

## Files to Modify

### 1. src/config.py
- Add `enabled: bool = True` to SyncConfig dataclass

### 2. src/main.py
- Modify sync thread to check `sync.enabled` before scheduling automatic syncs
- Manual sync via API should still work regardless of enabled setting

### 3. src/web/app.py
- Add endpoint for stopping sync: `POST /api/sync/stop`
- Ensure sync settings are included in config API responses
- Add endpoint to save sync settings if not already present

### 4. src/web/templates/settings.html
- Restructure Control tab:
  - Add `<h2>Album Sync</h2>` section
  - Add sync settings form fields
  - Add SYNC NOW button
  - Add STOP SYNC button (conditionally visible)
  - Move and rename metadata checkbox
  - Keep progress bar display

### 5. src/web/static/style.css
- Style for STOP SYNC button (red/danger)
- Any new form field styles needed

### 6. config.yaml (both dev and deployed)
- Add `enabled: true` to sync section
- Update documentation

### 7. src/cache_manager.py (maybe)
- Add method to stop/cancel ongoing sync if not already present

## UI Layout Sketch

```
┌─────────────────────────────────────────────────────────┐
│ Album Sync                                               │
├─────────────────────────────────────────────────────────┤
│ [✓] Enable Automatic Syncs                               │
│                                                          │
│ Sync Interval: [Daily (24h) ▼]                          │
│ Sync Time:     [03:00    ]                              │
│ [✓] Sync on service start                               │
│ Max Dimension: [0        ] (0 = full resolution)        │
│                                                          │
│ [  SYNC NOW  ]  [ STOP SYNC ]  (red, only when syncing) │
│                                                          │
│ [ ] Also update Google Photos metadata                   │
│     Fetches captions/locations from Google (slower)     │
│                                                          │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Syncing: Downloading photos...                      │ │
│ │ Album: Gaile & Luc's Favorites (3/7)               │ │
│ │ [████████████░░░░░░░░░░░░░░░░░░░░] 45%             │ │
│ │ Downloaded: 234 / 520                               │ │
│ └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## Implementation Order
1. Add `enabled` to SyncConfig in config.py
2. Update main.py to respect `sync.enabled`
3. Add stop sync endpoint to app.py
4. Update settings.html with new UI structure
5. Add styles to style.css
6. Update both config.yaml files
7. Test (without restarting service - just verify code)
8. Deploy and restart AFTER current sync completes

## Recent Commits (for context)
- d04bd36 - Add critical warning about avoiding unnecessary syncs
- a806489 - Fix legacy migration: full_resolution takes precedence
- 8a67caa - Simplify sync settings: remove full_resolution
- 5f87cd1 - Add CONTROL section to config, renumber sections
- c88dcb6 - Add event-based scheduling with holidays support

## Config Structure After Changes

```yaml
sync:
  enabled: true                        # NEW: Enable automatic syncs
  interval_minutes: 1440               # How often (1440 = daily, 0 = disabled)
  sync_time: "03:00"                   # Time for first sync
  sync_on_start: false                 # Sync on service start
  max_dimension: 0                     # 0 = full res, or max pixels
```
