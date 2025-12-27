#!/usr/bin/env python3
# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""
PhotoLoop Health Check Script

Non-disruptive health checks for the running PhotoLoop system.
Can be run manually or scheduled (e.g., via cron).

Usage:
    python scripts/health_check.py              # Run all checks
    python scripts/health_check.py --quick      # Quick checks only
    python scripts/health_check.py --verbose    # Detailed output
    python scripts/health_check.py --json       # JSON output for automation

Exit codes:
    0 - All checks passed
    1 - Some checks failed
    2 - Critical failure
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add project root to path - handle both dev and installed locations
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent

# Development: /home/user/photoloop/scripts/health_check.py -> src/
# Installed: /opt/photoloop/scripts/health_check.py -> photoloop/src/
sys.path.insert(0, str(PROJECT_ROOT))

# For installed location, also add the photoloop package path
installed_src = PROJECT_ROOT / "photoloop"
if installed_src.exists():
    sys.path.insert(0, str(installed_src))


@dataclass
class CheckResult:
    """Result of a health check."""
    name: str
    passed: bool
    message: str
    details: Optional[str] = None
    critical: bool = False


@dataclass
class HealthReport:
    """Complete health report."""
    timestamp: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    results: List[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult):
        self.results.append(result)
        if result.passed:
            self.passed += 1
        elif result.message == "SKIPPED":
            self.skipped += 1
        else:
            self.failed += 1

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    @property
    def has_critical_failure(self) -> bool:
        return any(r.critical and not r.passed for r in self.results)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "summary": {
                "passed": self.passed,
                "failed": self.failed,
                "skipped": self.skipped,
                "status": "PASS" if self.all_passed else "FAIL"
            },
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "message": r.message,
                    "details": r.details,
                    "critical": r.critical
                }
                for r in self.results
            ]
        }


class HealthChecker:
    """Runs health checks against PhotoLoop system."""

    def __init__(
        self,
        cache_dir: Path = Path("/var/lib/photoloop/cache"),
        config_path: Path = Path("/etc/photoloop/config.yaml")
    ):
        self.cache_dir = cache_dir
        self.config_path = config_path
        self.report = HealthReport(timestamp=datetime.now().isoformat())

    def check(self, name: str, critical: bool = False):
        """Decorator for check functions."""
        def decorator(func):
            def wrapper():
                try:
                    passed, message, details = func()
                    result = CheckResult(
                        name=name,
                        passed=passed,
                        message=message,
                        details=details,
                        critical=critical
                    )
                except Exception as e:
                    result = CheckResult(
                        name=name,
                        passed=False,
                        message=f"Exception: {e}",
                        critical=critical
                    )
                self.report.add(result)
                return result
            return wrapper
        return decorator

    def run_all(self, quick: bool = False) -> HealthReport:
        """Run all health checks."""

        # Critical checks
        self._check_service_running()
        self._check_config_exists()
        self._check_config_valid()

        # Metadata checks
        self._check_metadata_valid()
        self._check_no_missing_files()

        if not quick:
            # More thorough checks
            self._check_no_orphaned_files()
            self._check_photos_cycling()
            self._check_no_recent_errors()
            self._check_memory_usage()
            self._check_cache_size()

        return self.report

    def _check_service_running(self):
        """Check if service is active."""
        result = subprocess.run(
            ["systemctl", "is-active", "photoloop"],
            capture_output=True,
            text=True
        )
        passed = result.stdout.strip() == "active"
        self.report.add(CheckResult(
            name="Service Running",
            passed=passed,
            message="Service is active" if passed else "Service is not running",
            critical=True
        ))

    def _check_config_exists(self):
        """Check if config file exists."""
        passed = self.config_path.exists()
        self.report.add(CheckResult(
            name="Config Exists",
            passed=passed,
            message="Config file found" if passed else f"Config not found at {self.config_path}",
            critical=True
        ))

    def _check_config_valid(self):
        """Check if config passes validation."""
        if not self.config_path.exists():
            self.report.add(CheckResult(
                name="Config Valid",
                passed=False,
                message="SKIPPED - no config file"
            ))
            return

        try:
            from src.config import load_config, validate_config
            config = load_config(str(self.config_path))
            errors = validate_config(config)

            passed = len(errors) == 0
            self.report.add(CheckResult(
                name="Config Valid",
                passed=passed,
                message="Config is valid" if passed else f"{len(errors)} validation error(s)",
                details="\n".join(errors) if errors else None,
                critical=True
            ))
        except Exception as e:
            self.report.add(CheckResult(
                name="Config Valid",
                passed=False,
                message=f"Failed to load config: {e}",
                critical=True
            ))

    def _check_metadata_valid(self):
        """Check if metadata is valid JSON."""
        meta_path = self.cache_dir / "metadata.json"
        if not meta_path.exists():
            self.report.add(CheckResult(
                name="Metadata Valid",
                passed=False,
                message="SKIPPED - no metadata file"
            ))
            return

        try:
            with open(meta_path) as f:
                data = json.load(f)

            has_media = "media" in data
            has_settings = "settings" in data
            passed = has_media and has_settings

            media_count = len(data.get("media", {}))
            active = sum(1 for m in data.get("media", {}).values() if not m.get("deleted"))

            self.report.add(CheckResult(
                name="Metadata Valid",
                passed=passed,
                message=f"Valid ({active} active / {media_count} total photos)",
                critical=True
            ))
        except json.JSONDecodeError as e:
            self.report.add(CheckResult(
                name="Metadata Valid",
                passed=False,
                message=f"Invalid JSON: {e}",
                critical=True
            ))

    def _check_no_missing_files(self):
        """Check for files referenced in metadata but missing on disk."""
        meta_path = self.cache_dir / "metadata.json"
        if not meta_path.exists():
            self.report.add(CheckResult(
                name="No Missing Files",
                passed=False,
                message="SKIPPED - no metadata file"
            ))
            return

        with open(meta_path) as f:
            data = json.load(f)

        missing = []
        for item in data.get("media", {}).values():
            local_path = item.get("local_path")
            if local_path and not item.get("deleted"):
                if not os.path.exists(local_path):
                    missing.append(os.path.basename(local_path))

        passed = len(missing) == 0
        self.report.add(CheckResult(
            name="No Missing Files",
            passed=passed,
            message="All files present" if passed else f"{len(missing)} missing file(s)",
            details=", ".join(missing[:5]) if missing else None
        ))

    def _check_no_orphaned_files(self):
        """Check for files on disk not in metadata."""
        meta_path = self.cache_dir / "metadata.json"
        if not meta_path.exists() or not self.cache_dir.exists():
            self.report.add(CheckResult(
                name="No Orphaned Files",
                passed=False,
                message="SKIPPED - missing metadata or cache dir"
            ))
            return

        with open(meta_path) as f:
            data = json.load(f)

        files_on_disk = set(f.name for f in self.cache_dir.glob("*.jpg"))
        files_in_meta = set(
            os.path.basename(m["local_path"])
            for m in data.get("media", {}).values()
        )

        orphaned = files_on_disk - files_in_meta
        passed = len(orphaned) <= 5  # Allow small tolerance

        self.report.add(CheckResult(
            name="No Orphaned Files",
            passed=passed,
            message=f"{len(orphaned)} orphaned file(s)" if orphaned else "No orphaned files",
            details=", ".join(list(orphaned)[:5]) if orphaned else None
        ))

    def _check_photos_cycling(self):
        """Check if photos are being displayed."""
        result = subprocess.run(
            ["journalctl", "-u", "photoloop", "--since", "2 minutes ago",
             "--no-pager", "-o", "cat"],
            capture_output=True,
            text=True
        )

        display_count = result.stdout.count("Displaying photo:")
        passed = display_count >= 2

        self.report.add(CheckResult(
            name="Photos Cycling",
            passed=passed,
            message=f"{display_count} photo(s) displayed in last 2 min",
        ))

    def _check_no_recent_errors(self):
        """Check for recent error logs."""
        result = subprocess.run(
            ["journalctl", "-u", "photoloop", "--since", "10 minutes ago",
             "--no-pager", "-o", "cat"],
            capture_output=True,
            text=True
        )

        # Count ERROR level messages (excluding transient ones)
        allowed = ["Timeout waiting for photo grid", "Connection refused"]
        errors = []
        for line in result.stdout.split('\n'):
            if " - ERROR - " in line:
                if not any(a in line for a in allowed):
                    errors.append(line.split(" - ERROR - ")[-1][:80])

        passed = len(errors) == 0
        self.report.add(CheckResult(
            name="No Recent Errors",
            passed=passed,
            message=f"{len(errors)} error(s) in last 10 min" if errors else "No errors",
            details="\n".join(errors[:3]) if errors else None
        ))

    def _check_memory_usage(self):
        """Check memory usage of main process."""
        result = subprocess.run(
            "ps -o rss= -p $(pgrep -f 'photoloop.src.main' | head -1) 2>/dev/null",
            capture_output=True,
            text=True,
            shell=True
        )

        if not result.stdout.strip():
            self.report.add(CheckResult(
                name="Memory Usage",
                passed=True,
                message="SKIPPED - could not get memory info"
            ))
            return

        rss_kb = int(result.stdout.strip())
        rss_mb = rss_kb / 1024
        # Allow up to 1GB when Chrome is loaded for scraping
        passed = rss_mb < 1000

        self.report.add(CheckResult(
            name="Memory Usage",
            passed=passed,
            message=f"{rss_mb:.0f} MB" + (" (high)" if not passed else ""),
        ))

    def _check_cache_size(self):
        """Check if cache is within size limit."""
        if not self.cache_dir.exists():
            self.report.add(CheckResult(
                name="Cache Size",
                passed=True,
                message="SKIPPED - no cache directory"
            ))
            return

        try:
            from src.config import load_config
            config = load_config(str(self.config_path))
            max_mb = config.cache.max_size_mb
        except Exception:
            max_mb = 1000  # Default

        total_bytes = sum(f.stat().st_size for f in self.cache_dir.glob("*.jpg"))
        total_mb = total_bytes / 1024 / 1024
        passed = total_mb < max_mb * 1.1  # 10% tolerance

        self.report.add(CheckResult(
            name="Cache Size",
            passed=passed,
            message=f"{total_mb:.0f} MB / {max_mb} MB limit",
        ))


def print_report(report: HealthReport, verbose: bool = False):
    """Print report to console."""
    print(f"\n{'=' * 50}")
    print(f"PhotoLoop Health Check - {report.timestamp[:19]}")
    print(f"{'=' * 50}\n")

    for result in report.results:
        status = "PASS" if result.passed else ("SKIP" if "SKIPPED" in result.message else "FAIL")
        icon = {"PASS": "\u2705", "FAIL": "\u274c", "SKIP": "\u23ed"}[status]

        print(f"{icon} {result.name}: {result.message}")

        if verbose and result.details:
            for line in result.details.split('\n'):
                print(f"    {line}")

    print(f"\n{'=' * 50}")
    status = "PASS" if report.all_passed else "FAIL"
    print(f"Summary: {report.passed} passed, {report.failed} failed, {report.skipped} skipped - {status}")
    print(f"{'=' * 50}\n")


def main():
    parser = argparse.ArgumentParser(description="PhotoLoop health check")
    parser.add_argument("--quick", action="store_true", help="Quick checks only")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--cache-dir", default="/var/lib/photoloop/cache")
    parser.add_argument("--config", default="/etc/photoloop/config.yaml")

    args = parser.parse_args()

    checker = HealthChecker(
        cache_dir=Path(args.cache_dir),
        config_path=Path(args.config)
    )

    report = checker.run_all(quick=args.quick)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print_report(report, verbose=args.verbose)

    # Exit code
    if report.has_critical_failure:
        sys.exit(2)
    elif not report.all_passed:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
