# ##################################################################
# detection module
# identifies project type and current state
# detects project type (web or swift), existing ios structure, and publish status
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState
from config import WEB_INDICATORS, SWIFT_INDICATORS, BUNDLE_ID_PREFIX
from utils import (
    print_info,
    print_success,
    print_warning,
    find_files,
    dir_exists,
    file_exists,
)


def detect_project_type(project_path: Path) -> tuple[str, str]:
    # ##################################################################
    # detect project type
    # detect if project is web-based or native swift
    # returns (type, reason)
    # Check for Swift/Xcode indicators first (they're more specific)
    for pattern in SWIFT_INDICATORS:
        matches = list(project_path.glob(pattern))
        if matches:
            return "swift", f"Found {pattern}: {matches[0].name}"

    # Check for web indicators
    for indicator in WEB_INDICATORS:
        if file_exists(project_path / indicator):
            return "web", f"Found {indicator}"

    # Check in src/ subdirectory
    src_dir = project_path / "src"
    if dir_exists(src_dir):
        for indicator in WEB_INDICATORS:
            if file_exists(src_dir / indicator):
                return "web", f"Found src/{indicator}"

    # Look for any HTML files
    html_files = list(project_path.glob("*.html"))
    if html_files:
        return "web", f"Found HTML files: {html_files[0].name}"

    # Look for any Swift files
    swift_files = list(project_path.glob("**/*.swift"))
    if swift_files:
        return "swift", f"Found Swift files: {swift_files[0].name}"

    # Default to web (more common for the use case)
    return "web", "No specific indicators found, defaulting to web"


def detect_existing_ios_project(project_path: Path) -> Path | None:
    # ##################################################################
    # detect existing ios project
    # check if there's already an ios/xcode project
    # Look for .xcodeproj
    xcodeproj = list(project_path.glob("*.xcodeproj"))
    if xcodeproj:
        return xcodeproj[0]

    # Look in ios/ subdirectory (common for web-wrapped apps)
    ios_dir = project_path / "ios"
    if dir_exists(ios_dir):
        xcodeproj = list(ios_dir.glob("*.xcodeproj"))
        if xcodeproj:
            return xcodeproj[0]

    return None


def detect_bundle_id(project_path: Path) -> str | None:
    # ##################################################################
    # detect bundle id
    # try to detect existing bundle id from project
    xcodeproj = detect_existing_ios_project(project_path)
    if not xcodeproj:
        return None

    # Look for bundle ID in pbxproj
    pbxproj = xcodeproj / "project.pbxproj"
    if file_exists(pbxproj):
        content = pbxproj.read_text()
        # Look for PRODUCT_BUNDLE_IDENTIFIER
        import re
        match = re.search(r'PRODUCT_BUNDLE_IDENTIFIER\s*=\s*"?([^";]+)"?', content)
        if match:
            return match.group(1)

    return None


def generate_bundle_id(project_name: str) -> str:
    # ##################################################################
    # generate bundle id
    # generate a bundle id from project name
    # Sanitize project name: lowercase, replace spaces/special chars with nothing
    # Bundle IDs must be alphanumeric with dots only (no dashes allowed)
    import re
    # First, replace common separators with nothing (camelCase-like)
    sanitized = re.sub(r'[-_\s]+(.)', lambda m: m.group(1).upper(), project_name.lower())
    # Remove any remaining non-alphanumeric characters
    sanitized = re.sub(r'[^a-zA-Z0-9]', '', sanitized)
    # Ensure it starts with a letter
    if sanitized and not sanitized[0].isalpha():
        sanitized = 'app' + sanitized
    return f"{BUNDLE_ID_PREFIX}{sanitized}"


def run(project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # run detection step
    # sets state.project_type and state.bundle_id (if not already set)
    print_info(f"Analyzing project: {project_path.name}")

    # Detect project type
    project_type, reason = detect_project_type(project_path)
    state.project_type = project_type
    print_success(f"Project type: {project_type} ({reason})")

    # Check for existing iOS project
    existing_ios = detect_existing_ios_project(project_path)
    if existing_ios:
        print_info(f"Found existing Xcode project: {existing_ios.name}")
        state.metadata["has_existing_ios"] = True
        state.metadata["xcode_project"] = str(existing_ios)
    else:
        print_info("No existing Xcode project found")
        state.metadata["has_existing_ios"] = False

    # Determine bundle ID
    if not state.bundle_id:
        existing_bundle_id = detect_bundle_id(project_path)
        if existing_bundle_id:
            state.bundle_id = existing_bundle_id
            print_info(f"Using existing bundle ID: {state.bundle_id}")
        else:
            state.bundle_id = generate_bundle_id(project_path.name)
            print_info(f"Generated bundle ID: {state.bundle_id}")

    # Gather file statistics
    all_files = list(project_path.rglob("*"))
    files_only = [f for f in all_files if f.is_file() and not str(f).startswith(str(project_path / ".git"))]

    state.metadata["file_count"] = len(files_only)
    print_info(f"Files in project: {len(files_only)}")

    # Detect main entry point for web projects
    if project_type == "web":
        if file_exists(project_path / "index.html"):
            state.metadata["entry_point"] = "index.html"
        elif file_exists(project_path / "src" / "index.html"):
            state.metadata["entry_point"] = "src/index.html"
        elif file_exists(project_path / "dist" / "index.html"):
            state.metadata["entry_point"] = "dist/index.html"
            state.metadata["needs_build"] = True

        if "entry_point" in state.metadata:
            print_info(f"Web entry point: {state.metadata['entry_point']}")

    return True
