#!/usr/bin/env python3
# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Command-line interface for PhotoLoop.
Provides commands to control and manage the photo frame.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple, List

import requests


DEFAULT_API_URL = "http://localhost:8080"


def get_api_url() -> str:
    """Get the API URL from environment or default."""
    return os.environ.get("PHOTOLOOP_API_URL", DEFAULT_API_URL)


def api_call(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """
    Make an API call to the PhotoLoop web interface.

    Args:
        endpoint: API endpoint (e.g., "/api/status").
        method: HTTP method.
        data: JSON data to send.

    Returns:
        Response as dict.
    """
    url = f"{get_api_url()}{endpoint}"

    try:
        if method == "GET":
            response = requests.get(url, timeout=5)
        elif method == "POST":
            response = requests.post(url, json=data, timeout=5)
        elif method == "DELETE":
            response = requests.delete(url, timeout=5)
        else:
            raise ValueError(f"Unknown method: {method}")

        return response.json()

    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to PhotoLoop service.")
        print(f"Make sure PhotoLoop is running and accessible at {get_api_url()}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_status(args):
    """Show current status."""
    status = api_call("/api/status")

    print("PhotoLoop Status")
    print("=" * 40)

    if "schedule" in status:
        sched = status["schedule"]
        print(f"State: {sched.get('state', 'unknown')}")

        if sched.get("has_override"):
            print("Override: Active")

        if sched.get("next_transition", {}).get("time"):
            print(f"Next: {sched['next_transition']['description']} at "
                  f"{sched['next_transition']['time'][:16]}")

        today = sched.get("today", {})
        print(f"Today: {today.get('day', '')} "
              f"({today.get('start_time', '')}-{today.get('end_time', '')})")

    if "cache" in status:
        cache = status["cache"]
        counts = cache.get("counts", {})
        print(f"\nCache: {counts.get('photos', 0)} photos, "
              f"{counts.get('videos', 0)} videos")
        print(f"Size: {cache.get('size_mb', 0)} MB")


def cmd_start(args):
    """Force slideshow on."""
    result = api_call("/api/control/start", "POST")
    if result.get("success"):
        print("Slideshow started (override active)")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")


def cmd_stop(args):
    """Force slideshow off."""
    result = api_call("/api/control/stop", "POST")
    if result.get("success"):
        print("Slideshow stopped (override active)")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")


def cmd_resume(args):
    """Resume normal schedule."""
    result = api_call("/api/control/resume", "POST")
    if result.get("success"):
        print("Resumed normal schedule")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")


def cmd_next(args):
    """Skip to next photo."""
    result = api_call("/api/control/next", "POST")
    if result.get("success"):
        print("Skipped to next photo")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")


def cmd_sync(args):
    """Trigger album sync."""
    print("Starting album sync...")
    result = api_call("/api/sync", "POST")
    if result.get("success"):
        print("Sync started. This may take a few minutes.")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")


def cmd_reload(args):
    """Reload configuration."""
    result = api_call("/api/control/reload", "POST")
    if result.get("success"):
        print("Configuration reloaded")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")


def cmd_albums(args):
    """List configured albums and local directories."""
    albums = api_call("/api/albums")

    if not albums:
        print("No albums configured.")
        return

    print("Configured Photo Sources")
    print("=" * 60)
    for i, album in enumerate(albums, 1):
        name = album.get("name") or "(unnamed)"
        album_type = album.get("type", "google_photos")
        enabled = album.get("enabled", True)
        status = "" if enabled else " [disabled]"

        if album_type == "local":
            path = album.get("path", "")
            print(f"{i}. {name} [Local]{status}")
            print(f"   Path: {path}")
        else:
            url = album.get("url", "")
            print(f"{i}. {name} [Google Photos]{status}")
            print(f"   URL: {url}")
        print()


def cmd_add_album(args):
    """Add a new Google Photos album."""
    result = api_call("/api/albums", "POST", {
        "url": args.url,
        "name": args.name or "",
        "type": "google_photos"
    })

    if result.get("success"):
        print(f"Album added: {args.url}")
        print("Run 'photoloop sync' to download photos.")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")


def cmd_add_local(args):
    """Add a local directory as a photo source."""
    # Expand and validate path
    path = os.path.expanduser(args.path)
    if not os.path.isabs(path):
        path = os.path.abspath(path)

    if not os.path.exists(path):
        print(f"Error: Path does not exist: {path}")
        sys.exit(1)

    if not os.path.isdir(path):
        print(f"Error: Path is not a directory: {path}")
        sys.exit(1)

    result = api_call("/api/albums", "POST", {
        "path": path,
        "name": args.name or os.path.basename(path),
        "type": "local"
    })

    if result.get("success"):
        print(f"Local directory added: {path}")
        print("Run 'photoloop sync' to index photos.")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")


def cmd_photos(args):
    """List cached photos."""
    photos = api_call("/api/photos")

    if not photos:
        print("No photos cached.")
        return

    print(f"Cached Photos ({len(photos)} shown)")
    print("=" * 60)

    for photo in photos[:20]:
        media_type = photo.get("type", "photo")
        caption = photo.get("caption", "")[:40] if photo.get("caption") else ""
        date = photo.get("date", "")[:10] if photo.get("date") else ""
        print(f"[{media_type}] {date} {caption}")


def cmd_reset_album(args):
    """Reset metadata for a specific album."""
    album_name = args.album

    # Get list of albums to find the index
    albums = api_call("/api/albums")

    # Find the album by name (case-insensitive partial match)
    matches = []
    for i, album in enumerate(albums):
        name = album.get("name", "")
        if album_name.lower() == name.lower():
            # Exact match - use this one
            matches = [(i, name)]
            break
        elif album_name.lower() in name.lower():
            matches.append((i, name))

    if not matches:
        print(f"Error: No album found matching '{album_name}'")
        print("\nAvailable albums:")
        for album in albums:
            print(f"  - {album.get('name', '(unnamed)')}")
        sys.exit(1)

    if len(matches) > 1:
        print(f"Error: Multiple albums match '{album_name}':")
        for _, name in matches:
            print(f"  - {name}")
        print("\nPlease provide a more specific name.")
        sys.exit(1)

    index, matched_name = matches[0]

    # Determine what to reset
    clear_captions = not args.locations_only
    clear_locations = not args.captions_only

    if args.captions_only and args.locations_only:
        print("Error: Cannot specify both --captions-only and --locations-only")
        sys.exit(1)

    # Confirm unless --yes flag
    if not args.yes:
        what = []
        if clear_captions:
            what.append("captions")
        if clear_locations:
            what.append("locations")
        print(f"This will reset {' and '.join(what)} for album: {matched_name}")
        response = input("Continue? [y/N] ").strip().lower()
        if response not in ('y', 'yes'):
            print("Cancelled.")
            sys.exit(0)

    # Make the API call
    result = api_call(f"/api/albums/{index}/reset", "POST", {
        "captions": clear_captions,
        "locations": clear_locations
    })

    if result.get("success"):
        count = result.get("photos_reset", 0)
        print(f"Reset metadata for {count} photos in '{matched_name}'")
        if clear_captions:
            print("  - Captions cleared (will re-fetch on next sync)")
        if clear_locations:
            print("  - Locations cleared (will re-geocode when displayed)")
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")


# ============================================================================
# Update Command
# ============================================================================

INSTALL_DIR = "/opt/photoloop"
VENV_PIP = f"{INSTALL_DIR}/venv/bin/pip"
REQUIREMENTS_FILE = f"{INSTALL_DIR}/requirements.txt"
# Development source directory (derived from this file's location: src/cli.py -> repo root)
SOURCE_DIR = str(Path(__file__).parent.parent)


def run_command(cmd: List[str], capture: bool = True) -> Tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=120
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "Command timed out"
    except Exception as e:
        return 1, "", str(e)


def check_outdated_packages() -> List[dict]:
    """Check for outdated Python packages in the venv."""
    returncode, stdout, stderr = run_command([VENV_PIP, "list", "--outdated", "--format=json"])
    if returncode != 0:
        return []
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return []


def check_git_status() -> Tuple[bool, str, str]:
    """
    Check if there are updates available in git.
    Returns (has_updates, current_commit, remote_commit).
    """
    # Check if we're in a git repo
    if not os.path.isdir(os.path.join(SOURCE_DIR, ".git")):
        return False, "", ""

    # Fetch latest from remote (quietly)
    run_command(["git", "-C", SOURCE_DIR, "fetch", "--quiet"])

    # Get current commit
    rc, current, _ = run_command(["git", "-C", SOURCE_DIR, "rev-parse", "--short", "HEAD"])
    current = current.strip() if rc == 0 else ""

    # Get remote commit
    rc, remote, _ = run_command(["git", "-C", SOURCE_DIR, "rev-parse", "--short", "origin/main"])
    remote = remote.strip() if rc == 0 else ""

    # Check if behind
    rc, behind, _ = run_command([
        "git", "-C", SOURCE_DIR, "rev-list", "--count", "HEAD..origin/main"
    ])
    has_updates = behind.strip() != "0" if rc == 0 else False

    return has_updates, current, remote


def cmd_update(args):
    """Check for and apply updates."""
    check_only = args.check

    print("PhotoLoop Update")
    print("=" * 50)
    print()

    updates_available = False

    # Check Python packages
    print("Checking Python packages...")
    outdated = check_outdated_packages()

    if outdated:
        updates_available = True
        print(f"  {len(outdated)} package(s) can be updated:")
        for pkg in outdated:
            print(f"    - {pkg['name']}: {pkg['version']} → {pkg['latest_version']}")
    else:
        print("  All Python packages are up to date.")

    print()

    # Check for PhotoLoop code updates (git)
    print("Checking PhotoLoop code...")
    has_git_updates, current, remote = check_git_status()

    if has_git_updates:
        updates_available = True
        print(f"  Code updates available: {current} → {remote}")
    elif current:
        print(f"  PhotoLoop code is up to date (commit: {current})")
    else:
        print("  Not installed from git, skipping code update check.")

    print()

    # Check system packages (just info, we don't auto-update these)
    print("System packages:")
    print("  Run 'sudo apt update && sudo apt upgrade' to update system packages.")

    print()

    if not updates_available:
        print("✓ Everything is up to date!")
        return

    if check_only:
        print("─" * 50)
        print("Run 'photoloop update' (without --check) to apply updates.")
        return

    # Apply updates
    print("─" * 50)
    print("Applying updates...")
    print()

    # Update Python packages
    if outdated:
        print("Updating Python packages...")
        returncode, stdout, stderr = run_command([
            VENV_PIP, "install", "--upgrade", "-r", REQUIREMENTS_FILE
        ])
        if returncode == 0:
            print("  ✓ Python packages updated")
        else:
            print(f"  ✗ Error updating packages: {stderr}")

    # Update PhotoLoop code
    if has_git_updates:
        print("Updating PhotoLoop code...")

        # Pull latest
        returncode, stdout, stderr = run_command([
            "git", "-C", SOURCE_DIR, "pull", "--ff-only"
        ])

        if returncode == 0:
            print("  ✓ Code updated")

            # Copy to install directory
            print("  Installing updated code...")
            returncode, _, stderr = run_command([
                "sudo", "cp", "-r", f"{SOURCE_DIR}/src/.", f"{INSTALL_DIR}/photoloop/src/"
            ])

            if returncode == 0:
                print("  ✓ Code installed")
            else:
                print(f"  ✗ Error installing code: {stderr}")
        else:
            print(f"  ✗ Error pulling updates: {stderr}")

    print()

    # Restart service
    print("Restarting PhotoLoop service...")
    returncode, _, stderr = run_command(["sudo", "systemctl", "restart", "photoloop"])

    if returncode == 0:
        print("  ✓ Service restarted")
    else:
        print(f"  ✗ Error restarting service: {stderr}")

    print()
    print("─" * 50)
    print("✓ Update complete!")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="PhotoLoop - Raspberry Pi Photo Frame",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  photoloop status              Show current status
  photoloop start               Force slideshow on
  photoloop stop                Force slideshow off
  photoloop resume              Resume schedule
  photoloop sync                Download/index photos
  photoloop albums              List configured sources
  photoloop add-album URL       Add a Google Photos album
  photoloop add-local PATH      Add a local directory
  photoloop reset-album NAME    Reset metadata for an album
  photoloop update --check      Check for available updates
  photoloop update              Apply available updates

Environment:
  PHOTOLOOP_API_URL    API URL (default: http://localhost:8080)
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Status
    subparsers.add_parser("status", help="Show current status")

    # Control commands
    subparsers.add_parser("start", help="Force slideshow on (override schedule)")
    subparsers.add_parser("stop", help="Force slideshow off")
    subparsers.add_parser("resume", help="Clear override, follow schedule")
    subparsers.add_parser("next", help="Skip to next photo")
    subparsers.add_parser("reload", help="Reload configuration")

    # Sync
    subparsers.add_parser("sync", help="Sync albums (download new photos)")

    # Albums
    subparsers.add_parser("albums", help="List configured photo sources")

    add_album = subparsers.add_parser("add-album", help="Add a Google Photos album")
    add_album.add_argument("url", help="Album URL")
    add_album.add_argument("--name", "-n", help="Album name")

    add_local = subparsers.add_parser("add-local", help="Add a local directory")
    add_local.add_argument("path", help="Path to local directory")
    add_local.add_argument("--name", "-n", help="Display name for this directory")

    # Photos
    subparsers.add_parser("photos", help="List cached photos")

    # Reset album metadata
    reset_album = subparsers.add_parser(
        "reset-album",
        help="Reset metadata for an album (clears captions/locations)"
    )
    reset_album.add_argument("album", help="Album name (partial match supported)")
    reset_album.add_argument(
        "--captions-only", "-c",
        action="store_true",
        help="Only reset captions (keep locations)"
    )
    reset_album.add_argument(
        "--locations-only", "-l",
        action="store_true",
        help="Only reset locations (keep captions)"
    )
    reset_album.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt"
    )

    # Update
    update_parser = subparsers.add_parser("update", help="Check for and apply updates")
    update_parser.add_argument(
        "--check", "-c",
        action="store_true",
        help="Check for updates without applying them"
    )

    # Parse and execute
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "status": cmd_status,
        "start": cmd_start,
        "stop": cmd_stop,
        "resume": cmd_resume,
        "next": cmd_next,
        "sync": cmd_sync,
        "reload": cmd_reload,
        "albums": cmd_albums,
        "add-album": cmd_add_album,
        "add-local": cmd_add_local,
        "photos": cmd_photos,
        "reset-album": cmd_reset_album,
        "update": cmd_update,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
