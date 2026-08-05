# ##################################################################
# icon module
# ai-generated app icon with all required sizes
# uses a deterministic prompt, the durable codex image queue, and pil to resize to all ios sizes
import json
import hashlib
from pathlib import Path
import shutil

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState
from config import ICON_SIZE, ICON_SIZES_IOS
from utils import (
    print_info,
    print_success,
    print_error,
    print_warning,
    run as exec_cmd,
    ensure_dir,
    file_exists,
    write_file,
)

GENERATE_IMAGE_CLI = "/Users/darrenoakey/bin/generate_image"
ICON_OPERATION_STATE = "icon-1024.image-job.json"
ICON_RESPONSE_CACHE = Path.home() / "Library" / "Caches" / "app-publish" / "icon-responses"


def icon_cache_path(prompt: str) -> Path:
    request = json.dumps(
        {"height": ICON_SIZE, "prompt": prompt, "transparent": False, "width": ICON_SIZE},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ICON_RESPONSE_CACHE / f"{hashlib.sha256(request).hexdigest()}.png"


def generate_icon_prompt(state: ProjectState) -> str:
    # ##################################################################
    # generate icon prompt
    # keep the durable request byte-identical across crashes and restarts
    description = state.app_description[:500] if state.app_description else state.project_name
    category = state.metadata.get("primary_category", "Utility")
    return (
        f"Professional iOS app icon for {state.app_name}, a {category} app described as: {description}. "
        "Create a simple centered symbol with strong clean lines, crisp edges, minimal detail, and bold vibrant "
        "saturated colors; recognizable from 29x29 through 1024x1024, suitable on light and dark backgrounds, "
        "with no text, letters, numbers, fine details, watermarks, or borders."
    )


def is_valid_master_icon(path: Path) -> bool:
    # ##################################################################
    # validate master icon
    # accept only a fully decoded png with the exact app store dimensions
    if not path.is_file():
        return False

    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            return image.format == "PNG" and image.size == (ICON_SIZE, ICON_SIZE)
    except (OSError, SyntaxError, ValueError):
        return False


def generate_master_icon(project_path: Path, state: ProjectState) -> Path | None:
    # ##################################################################
    # generate master icon
    # generate the master icon through the one durable codex image path
    assets_dir = project_path / "assets"
    ensure_dir(assets_dir)

    master_icon = assets_dir / "icon-1024.png"
    operation_state = assets_dir / ICON_OPERATION_STATE

    if is_valid_master_icon(master_icon):
        print_info("Valid 1024x1024 PNG master icon already exists")
        return master_icon
    if file_exists(master_icon):
        print_warning(f"Existing master icon is invalid and will be recovered in place: {master_icon}")

    icon_prompt = generate_icon_prompt(state)
    print_info(f"Icon prompt: {icon_prompt[:80]}...")
    cached_icon = icon_cache_path(icon_prompt)
    if is_valid_master_icon(cached_icon):
        master_icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached_icon, master_icon)
        master_icon.chmod(0o600)
        print_success(f"Recovered master icon from real-response cache: {master_icon}")
        return master_icon

    print_info("Generating icon image through the durable Mac-mini Codex queue...")
    ret_code, output = exec_cmd(
        [
            GENERATE_IMAGE_CLI,
            "--output",
            str(master_icon),
            "--width",
            str(ICON_SIZE),
            "--height",
            str(ICON_SIZE),
            "--state-file",
            str(operation_state),
            "--prompt",
            icon_prompt,
        ]
    )

    if ret_code != 0:
        print_error(f"Failed to generate icon: {output}")
        return None
    if not is_valid_master_icon(master_icon):
        print_error(f"Image service did not persist a valid {ICON_SIZE}x{ICON_SIZE} PNG at {master_icon}: {output}")
        return None

    cached_icon.parent.mkdir(parents=True, exist_ok=True)
    temporary_cache = cached_icon.with_suffix(".tmp")
    shutil.copyfile(master_icon, temporary_cache)
    temporary_cache.replace(cached_icon)
    cached_icon.chmod(0o600)

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
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Generate all sizes
    contents = {"images": [], "info": {"author": "app-publish", "version": 1}}

    for size, scale, idiom, suffix in ICON_SIZES_IOS:
        pixel_size = int(size * scale)
        filename = f"icon-{suffix}.png"

        # Resize
        resized = img.resize((pixel_size, pixel_size), Image.Resampling.LANCZOS)
        resized.save(icons_dir / filename, "PNG")

        # Add to Contents.json
        contents["images"].append(
            {
                "filename": filename,
                "idiom": idiom,
                "scale": f"{scale}x",
                "size": f"{size}x{size}",
            }
        )

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
            icon_1024_patterns = [
                "Icon-1024.png",
                "icon-1024.png",
                "icon-ios-marketing-1024x1024@1x.png",
                "AppIcon-1024.png",
            ]
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
    # returns immediately when valid icons already exist in the Xcode project
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
