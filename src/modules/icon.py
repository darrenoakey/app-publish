# ##################################################################
# icon module
# ai-generated app icon with all required sizes
# uses claude for icon prompts, generate_flux for master icon, and pil to resize to all ios sizes
import json
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState
from config import ICON_SIZE, ICON_SIZES_IOS
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    llm_chat,
    ensure_dir,
    file_exists,
    write_file,
)


def generate_icon_prompt(state: ProjectState) -> str:
    # ##################################################################
    # generate icon prompt
    # use ai to create an icon generation prompt
    prompt = f"""Create an image generation prompt for an iOS app icon.

App name: {state.app_name}
Description: {state.app_description[:500] if state.app_description else state.project_name}
Category: {state.metadata.get('primary_category', 'Utility')}

Generate a prompt for a professional iOS app icon that:
- Is simple and recognizable at small sizes (29x29 to 1024x1024)
- Has strong, clean lines and crisp edges that scale well when resized
- Uses bold, vibrant, saturated colors
- Has a clean, modern design with minimal detail
- Works well on both light and dark backgrounds
- Does NOT include any text or fine details that blur at small sizes
- Has a simple centered symbol or graphic
- Matches the app's theme (cheerful for games, professional for utilities, etc.)

Respond with ONLY the image generation prompt (1-2 sentences), no other text.
"""
    return llm_chat(prompt)


def generate_master_icon(project_path: Path, state: ProjectState) -> Path | None:
    # ##################################################################
    # generate master icon
    # generate the master 1024x1024 icon using generate_flux
    assets_dir = project_path / "assets"
    ensure_dir(assets_dir)

    master_icon = assets_dir / "icon-1024.jpg"

    if file_exists(master_icon):
        print_info("Master icon already exists")
        return master_icon

    # Generate icon prompt
    print_info("Generating icon concept...")
    icon_prompt = generate_icon_prompt(state)
    if not icon_prompt:
        icon_prompt = f"Professional iOS app icon for {state.app_name}, simple modern design, bold colors, no text"

    print_info(f"Icon prompt: {icon_prompt[:80]}...")

    # Use generate_flux to create the icon (25 steps for crisp, detailed output)
    print_info("Generating icon image (this takes ~14 minutes)...")
    ret_code, output = exec_cmd([
        "generate_flux",
        "--output", str(master_icon),
        "--width", str(ICON_SIZE),
        "--height", str(ICON_SIZE),
        "--steps", "25",
        "--prompt", icon_prompt,
    ], timeout=1200)

    if ret_code != 0 or not file_exists(master_icon):
        print_error(f"Failed to generate icon: {output}")
        return None

    print_success(f"Master icon generated: {master_icon}")
    return master_icon


def resize_icons(master_icon: Path, project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # resize icons
    # resize master icon to all required ios sizes
    try:
        from PIL import Image
    except ImportError:
        print_error("PIL not installed. Run: pip install Pillow")
        return False

    # Determine output directory (in Xcode project)
    xcode_project = state.metadata.get("xcode_project", "")
    if xcode_project:
        # Find Assets.xcassets
        xcode_path = Path(xcode_project).parent
        assets_paths = list(xcode_path.rglob("Assets.xcassets"))
        if assets_paths:
            icons_dir = assets_paths[0] / "AppIcon.appiconset"
        else:
            icons_dir = project_path / "ios" / "App" / "App" / "Assets.xcassets" / "AppIcon.appiconset"
    else:
        icons_dir = project_path / "ios" / "App" / "App" / "Assets.xcassets" / "AppIcon.appiconset"

    ensure_dir(icons_dir)

    print_info(f"Resizing icons to {icons_dir}...")

    # Load master icon
    img = Image.open(master_icon)
    if img.mode != 'RGBA':
        img = img.convert('RGBA')

    # Generate all sizes
    contents = {"images": [], "info": {"author": "app-publish", "version": 1}}

    for size, scale, idiom, suffix in ICON_SIZES_IOS:
        pixel_size = int(size * scale)
        filename = f"icon-{suffix}.png"

        # Resize
        resized = img.resize((pixel_size, pixel_size), Image.Resampling.LANCZOS)
        resized.save(icons_dir / filename, "PNG")

        # Add to Contents.json
        contents["images"].append({
            "filename": filename,
            "idiom": idiom,
            "scale": f"{scale}x",
            "size": f"{size}x{size}",
        })

    # Write Contents.json
    write_file(icons_dir / "Contents.json", json.dumps(contents, indent=2))

    print_success(f"Generated {len(ICON_SIZES_IOS)} icon sizes")
    return True


def check_existing_icons(project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # check existing icons
    # check if valid app icons already exist in the xcode project
    xcode_project = state.metadata.get("xcode_project", "")
    if not xcode_project:
        return False

    xcode_path = Path(xcode_project).parent
    assets_paths = list(xcode_path.rglob("Assets.xcassets"))

    for assets_path in assets_paths:
        appiconset = assets_path / "AppIcon.appiconset"
        if appiconset.exists():
            # Check for 1024x1024 icon (the master icon required for App Store)
            icon_1024_patterns = ["Icon-1024.png", "icon-1024.png", "icon-ios-marketing-1024x1024@1x.png", "AppIcon-1024.png"]
            for pattern in icon_1024_patterns:
                if (appiconset / pattern).exists():
                    print_info(f"Found existing 1024x1024 icon: {appiconset / pattern}")
                    return True

            # Also check for any PNG files larger than 512x512
            for png_file in appiconset.glob("*.png"):
                try:
                    from PIL import Image
                    with Image.open(png_file) as img:
                        if img.width >= 1024 and img.height >= 1024:
                            print_info(f"Found existing large icon: {png_file}")
                            return True
                except Exception:
                    continue

    return False


def run(project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # run icon generation step
    # creates assets/icon-1024.png (master icon) and all sized icons in xcode project
    # skips if valid icons already exist in xcode project
    # Check for existing icons first
    if check_existing_icons(project_path, state):
        print_success("App icons already exist in Xcode project")
        state.metadata["icon_generated"] = True
        state.metadata["icon_existing"] = True
        return True

    # Generate master icon
    master_icon = generate_master_icon(project_path, state)
    if not master_icon:
        return False

    # Resize to all required sizes
    if not resize_icons(master_icon, project_path, state):
        return False

    state.metadata["icon_generated"] = True
    return True
