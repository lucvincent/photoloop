#!/usr/bin/env python3
# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
Command-line interface for PhotoLoop.
Provides commands to control and manage the photo frame.
"""

import argparse
import json
import os
import sys
from typing import Optional

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
    """List configured albums."""
    albums = api_call("/api/albums")

    if not albums:
        print("No albums configured.")
        return

    print("Configured Albums")
    print("=" * 60)
    for i, album in enumerate(albums, 1):
        name = album.get("name") or "(unnamed)"
        url = album.get("url", "")
        print(f"{i}. {name}")
        print(f"   {url}")
        print()


def cmd_add_album(args):
    """Add a new album."""
    result = api_call("/api/albums", "POST", {
        "url": args.url,
        "name": args.name or ""
    })

    if result.get("success"):
        print(f"Album added: {args.url}")
        print("Run 'photoloop sync' to download photos.")
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


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="PhotoLoop - Raspberry Pi Photo Frame",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  photoloop status          Show current status
  photoloop start           Force slideshow on
  photoloop stop            Force slideshow off
  photoloop resume          Resume schedule
  photoloop sync            Download new photos
  photoloop albums          List configured albums
  photoloop add-album URL   Add a new album

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
    subparsers.add_parser("albums", help="List configured albums")

    add_album = subparsers.add_parser("add-album", help="Add a new album")
    add_album.add_argument("url", help="Album URL")
    add_album.add_argument("--name", "-n", help="Album name")

    # Photos
    subparsers.add_parser("photos", help="List cached photos")

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
        "photos": cmd_photos,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
