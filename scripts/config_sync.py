#!/usr/bin/env python3
"""
Config synchronization tool for PhotoLoop.

Validates that config.yaml documents all options from config.py,
and can generate a template config file from the code.

Usage:
    python scripts/config_sync.py validate    # Check for missing options
    python scripts/config_sync.py generate    # Generate config template
    python scripts/config_sync.py diff        # Show what's missing
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml


@dataclass
class ConfigField:
    """Represents a configuration field from a dataclass."""
    name: str
    field_type: str
    default: Any
    parent_class: str
    docstring: Optional[str] = None


# Documentation for each config section and field
# This serves as the source of truth for human-readable docs
FIELD_DOCS = {
    "albums": {
        "_section": "Albums - List of public Google Photos album URLs to display",
        "_description": """Add one or more public (world-visible) Google Photos albums.
You can use either the short URL (photos.app.goo.gl/...) or
the full URL (photos.google.com/share/...)

To make an album public:
  1. Open Google Photos and go to the album
  2. Click Share > Get link
  3. Set "Link sharing" to ON
  4. Copy the link and paste it below""",
    },
    "sync": {
        "_section": "Sync Settings - How often to check for new photos/videos",
        "interval_minutes": "Check albums for new content (0 = manual only)",
        "full_resolution": "true = original quality, false = limit to max_dimension",
        "max_dimension": "Max width/height if full_resolution is false (use 3840 for 4K)",
    },
    "display": {
        "_section": "Display Settings - Slideshow appearance and behavior",
        "resolution": '"auto" detects screen size, or specify "1920x1080", "3840x2160"',
        "photo_duration_seconds": "How long each photo is shown",
        "video_enabled": "Set to false to skip videos entirely",
        "transition_type": "Options: fade, slide_left, slide_right, slide_up, slide_down, random",
        "transition_duration_ms": "Transition animation length in milliseconds",
        "order": "Options: random, sequential",
    },
    "scaling": {
        "_section": "Scaling and Cropping - How photos fill the screen",
        "mode": '''Scaling mode:
  "fill"     - Fill screen, crop excess (no bars)
  "fit"      - Fit within screen (may have bars)
  "balanced" - Crop up to max_crop_percent, then show bars
  "stretch"  - Stretch to fill (distorts aspect ratio)''',
        "max_crop_percent": "For balanced mode: max % to crop before showing bars (0-50)",
        "background_color": "RGB color for letterbox/pillarbox bars [R, G, B]",
        "smart_crop": "Enable intelligent crop region selection",
        "face_detection": "Detect faces to keep them visible when cropping",
        "face_position": 'Where to position faces: "center", "rule_of_thirds", "top_third"',
        "fallback_crop": 'Crop position when no faces: "center", "top", "bottom"',
    },
    "ken_burns": {
        "_section": "Ken Burns Effect - Slow zoom/pan during photo display",
        "_description": "Adds subtle motion to still photos by slowly zooming and panning.",
        "enabled": "Enable/disable Ken Burns effect",
        "zoom_range": "Zoom range [min, max], e.g. [1.0, 1.15] = 0-15% zoom",
        "pan_speed": "Pan speed as fraction per second (0.02 = 2%/sec)",
        "randomize": "Randomize zoom direction and pan path",
    },
    "overlay": {
        "_section": "Overlay Settings - Photo date and caption display",
        "enabled": "Show photo information overlay",
        "show_date": "Show photo date from EXIF/metadata",
        "show_caption": "Show description/caption if available",
        "date_format": 'Python strftime format, e.g. "%B %d, %Y" = "January 15, 2024"',
        "position": 'Screen position: "bottom_left", "bottom_right", "top_left", "top_right"',
        "font_size": "Font size in pixels",
        "font_color": "RGB text color [R, G, B]",
        "background_color": "RGBA background [R, G, B, Alpha] (Alpha: 0=transparent, 255=opaque)",
        "padding": "Distance from screen edge in pixels",
        "max_caption_length": "Truncate long captions (0 = no limit)",
    },
    "schedule": {
        "_section": "Schedule - When to display slideshow vs off-hours mode",
        "enabled": "Enable/disable scheduling",
        "off_hours_mode": '"black" = turn display black, "clock" = show clock',
        "weekday": "Schedule for Monday-Friday (start_time, end_time in HH:MM)",
        "weekend": "Schedule for Saturday-Sunday",
        "overrides": "Override schedule for specific days",
    },
    "cache": {
        "_section": "Cache Settings - Local storage for photos/videos",
        "directory": "Where to store downloaded photos and videos",
        "max_size_mb": "Maximum cache size in MB (oldest removed when exceeded)",
    },
    "web": {
        "_section": "Web Interface - Remote configuration via browser",
        "enabled": "Enable/disable web interface",
        "port": "Web interface port number",
        "host": '"0.0.0.0" = network accessible, "127.0.0.1" = localhost only',
    },
}


def parse_config_dataclasses(config_path: Path) -> Dict[str, List[ConfigField]]:
    """
    Parse config.py and extract all dataclass fields.

    Returns dict mapping section name to list of fields.
    """
    with open(config_path) as f:
        source = f.read()

    tree = ast.parse(source)

    sections = {}

    # Map class names to YAML section names
    CLASS_TO_SECTION = {
        'AlbumConfig': 'albums',
        'SyncConfig': 'sync',
        'DisplayConfig': 'display',
        'ScalingConfig': 'scaling',
        'KenBurnsConfig': 'ken_burns',
        'OverlayConfig': 'overlay',
        'ScheduleConfig': 'schedule',
        'CacheConfig': 'cache',
        'WebConfig': 'web',
    }

    # Skip these helper dataclasses (not top-level config sections)
    SKIP_CLASSES = {'ScheduleTimeConfig', 'PhotoLoopConfig'}

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # Check if it's a dataclass (has @dataclass decorator)
            is_dataclass = any(
                (isinstance(d, ast.Name) and d.id == 'dataclass') or
                (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == 'dataclass')
                for d in node.decorator_list
            )

            if not is_dataclass:
                continue

            class_name = node.name
            if class_name in SKIP_CLASSES:
                continue

            # Map class name to config section
            section_name = CLASS_TO_SECTION.get(class_name, class_name.replace('Config', '').lower())

            fields = []

            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field_name = item.target.id

                    # Get type annotation
                    field_type = ast.unparse(item.annotation) if item.annotation else "Any"

                    # Get default value
                    default = None
                    if item.value:
                        try:
                            # Try to evaluate simple defaults
                            default = ast.literal_eval(ast.unparse(item.value))
                        except (ValueError, SyntaxError):
                            # Complex default (like field(default_factory=...))
                            default = ast.unparse(item.value)

                    fields.append(ConfigField(
                        name=field_name,
                        field_type=field_type,
                        default=default,
                        parent_class=class_name,
                    ))

            if fields:
                sections[section_name] = fields

    return sections


def parse_config_yaml(yaml_path: Path) -> Set[Tuple[str, str]]:
    """
    Parse config.yaml and return set of (section, field) tuples that are documented.
    """
    with open(yaml_path) as f:
        content = f.read()

    try:
        config = yaml.safe_load(content)
    except yaml.YAMLError:
        config = {}

    documented = set()

    def extract_keys(obj, prefix=""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                full_key = f"{prefix}.{key}" if prefix else key
                documented.add((prefix, key) if prefix else (key, ""))
                if isinstance(value, dict) and prefix == "":
                    # Only go one level deep for sections
                    for subkey in value.keys():
                        documented.add((key, subkey))

    if config:
        extract_keys(config)

    return documented


def validate_config(config_py: Path, config_yaml: Path) -> List[str]:
    """
    Validate that all options in config.py are documented in config.yaml.

    Returns list of missing options.
    """
    code_sections = parse_config_dataclasses(config_py)
    documented = parse_config_yaml(config_yaml)

    # Fields that are handled specially and don't need direct documentation
    SPECIAL_FIELDS = {
        ('albums', 'url'),    # Part of album list items
        ('albums', 'name'),   # Part of album list items
        ('schedule', 'overrides'),  # Optional, often commented out
    }

    missing = []

    for section, fields in code_sections.items():
        # Check if section exists
        if not any(s == section for s, _ in documented):
            missing.append(f"Section '{section}' is not documented")
            continue

        # Check each field
        for field in fields:
            if (section, field.name) in SPECIAL_FIELDS:
                continue
            if (section, field.name) not in documented:
                missing.append(f"{section}.{field.name} (default: {field.default})")

    return missing


def generate_config_template(config_py: Path) -> str:
    """
    Generate a config.yaml template from config.py dataclasses.
    """
    code_sections = parse_config_dataclasses(config_py)

    lines = [
        "# ============================================================",
        "# PhotoLoop Configuration",
        "# ============================================================",
        "#",
        "# This file controls all aspects of your PhotoLoop photo frame.",
        "# Edit this file directly or use the web interface at:",
        "#   http://<raspberry-pi-ip>:8080",
        "#",
        "# After editing, restart PhotoLoop or use 'photoloop reload'",
        "# ============================================================",
        "",
    ]

    # Order sections logically
    section_order = ['albums', 'sync', 'display', 'scaling', 'ken_burns', 'overlay', 'schedule', 'cache', 'web']

    for section in section_order:
        if section not in code_sections:
            continue

        fields = code_sections[section]
        docs = FIELD_DOCS.get(section, {})

        # Section header
        section_title = docs.get('_section', f'{section.title()} Settings')
        lines.append("# " + "-" * 60)
        lines.append(f"# {section_title}")
        lines.append("# " + "-" * 60)

        if '_description' in docs:
            for desc_line in docs['_description'].split('\n'):
                lines.append(f"# {desc_line}")

        # Handle albums specially (it's a list)
        if section == 'albums':
            lines.append("albums:")
            lines.append('  - url: "https://photos.app.goo.gl/YOUR_ALBUM_URL_HERE"')
            lines.append('    name: "My Photos"')
            lines.append("")
            continue

        lines.append(f"{section}:")

        for field in fields:
            field_doc = docs.get(field.name, "")

            # Add comment if we have docs
            if field_doc:
                # Handle multi-line docs
                doc_lines = field_doc.split('\n')
                for doc_line in doc_lines:
                    lines.append(f"  # {doc_line}")

            # Format the default value
            default = field.default
            if default is None:
                default_str = "null"
            elif isinstance(default, bool):
                default_str = str(default).lower()
            elif isinstance(default, str):
                default_str = f'"{default}"'
            elif isinstance(default, list):
                default_str = str(default).replace("'", '"')
            else:
                default_str = str(default)

            lines.append(f"  {field.name}: {default_str}")
            lines.append("")

        lines.append("")

    return '\n'.join(lines)


def show_diff(config_py: Path, config_yaml: Path) -> None:
    """Show what options are missing from config.yaml."""
    missing = validate_config(config_py, config_yaml)

    if not missing:
        print("All config options are documented in config.yaml")
        return

    print(f"Missing {len(missing)} option(s) in config.yaml:\n")
    for option in missing:
        print(f"  - {option}")
    print()
    print("Add these to config.yaml or run 'generate' to create a new template.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    # Find paths relative to script location
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    config_py = project_root / "src" / "config.py"
    config_yaml = project_root / "config.yaml"

    if command == "validate":
        missing = validate_config(config_py, config_yaml)
        if missing:
            print(f"WARN: {len(missing)} undocumented option(s):")
            for option in missing:
                print(f"  - {option}")
            sys.exit(1)
        else:
            print("OK: All config options are documented")
            sys.exit(0)

    elif command == "generate":
        template = generate_config_template(config_py)
        output_path = project_root / "config.template.yaml"
        with open(output_path, 'w') as f:
            f.write(template)
        print(f"Generated template: {output_path}")
        print("Review and merge into config.yaml as needed.")

    elif command == "diff":
        show_diff(config_py, config_yaml)

    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
