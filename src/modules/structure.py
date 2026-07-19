# ##################################################################
# structure module
# reorganizes project files and creates ios project structure
# for web projects: moves web files, initializes capacitor, creates ios directory
# for swift projects: ensures standard xcode structure, creates fastlane directory
import shutil
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    ensure_dir,
    file_exists,
    dir_exists,
    write_file,
)


def setup_web_project(project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # setup web project
    # set up ios project structure for a web-based project using capacitor
    print_info("Setting up web project for iOS...")

    # Check for package.json
    package_json = project_path / "package.json"
    if not file_exists(package_json):
        # Create a minimal package.json
        print_info("Creating package.json...")
        package_content = f'''{{
  "name": "{state.project_name}",
  "version": "1.0.0",
  "description": "{state.app_description or "iOS App"}",
  "main": "index.html",
  "scripts": {{
    "build": "echo 'No build step required'"
  }}
}}
'''
        write_file(package_json, package_content)

    # Check if Capacitor packages are installed
    node_modules = project_path / "node_modules" / "@capacitor"
    if not dir_exists(node_modules):
        # Install Capacitor
        print_info("Installing Capacitor...")
        ret_code, output = exec_cmd(
            ["npm", "install", "@capacitor/core", "@capacitor/cli", "@capacitor/ios"],
            cwd=project_path,
        )
        if ret_code != 0:
            print_error(f"Failed to install Capacitor: {output}")
            return False
        print_success("Capacitor installed")
    else:
        print_info("Capacitor already installed")

    # Always update capacitor.config.json to ensure bundle ID is correct
    print_info("Updating Capacitor configuration...")
    app_name = state.app_name or state.project_name

    # Determine web directory (where index.html is)
    # Capacitor requires webDir to be a subdirectory, not "."
    web_dir = None
    if file_exists(project_path / "dist" / "index.html"):
        web_dir = "dist"
    elif file_exists(project_path / "build" / "index.html"):
        web_dir = "build"
    elif file_exists(project_path / "www" / "index.html"):
        web_dir = "www"
    elif file_exists(project_path / "public" / "index.html"):
        web_dir = "public"

    # If index.html is in root, create www directory and MOVE web files
    if web_dir is None and file_exists(project_path / "index.html"):
        print_info("Moving web assets to www/ directory...")
        www_dir = project_path / "www"
        ensure_dir(www_dir)

        # Move web files to www (html, css, js, images) - MOVE not copy to avoid duplicates
        web_extensions = {
            ".html",
            ".css",
            ".js",
            ".json",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".svg",
            ".ico",
            ".woff",
            ".woff2",
            ".ttf",
        }
        files_to_move = []
        dirs_to_move = []

        for f in project_path.iterdir():
            if f.is_file() and f.suffix.lower() in web_extensions:
                files_to_move.append(f)
            elif f.is_dir() and f.name not in {
                "node_modules",
                "ios",
                "android",
                "www",
                ".git",
                "fastlane",
                "build",
                "assets",
                "scripts",
            }:
                # Check if directory contains web assets
                if any(sub.suffix.lower() in web_extensions for sub in f.rglob("*") if sub.is_file()):
                    dirs_to_move.append(f)

        # Move files
        for f in files_to_move:
            shutil.move(str(f), str(www_dir / f.name))

        # Move directories
        for d in dirs_to_move:
            shutil.move(str(d), str(www_dir / d.name))

        web_dir = "www"
        print_success(f"Moved {len(files_to_move)} files and {len(dirs_to_move)} directories to www/")

    # Root directory is the final source candidate
    if web_dir is None:
        print_error("No index.html found in project")
        return False

    # Create/update capacitor.config.json with correct bundle ID
    capacitor_config_content = f'''{{
  "appId": "{state.bundle_id}",
  "appName": "{app_name}",
  "webDir": "{web_dir}",
  "server": {{
    "androidScheme": "https"
  }},
  "ios": {{
    "path": "ios"
  }}
}}
'''
    write_file(project_path / "capacitor.config.json", capacitor_config_content)
    print_success("Capacitor configured")

    # Add iOS platform if not present
    ios_dir = project_path / "ios"
    if dir_exists(ios_dir):
        print_info("iOS platform already exists")
    else:
        print_info("Adding iOS platform...")
        ret_code, output = exec_cmd(
            ["npx", "cap", "add", "ios"],
            cwd=project_path,
        )
        if ret_code != 0:
            print_error(f"Failed to add iOS platform: {output}")
            return False
        print_success("iOS platform added")

    # Sync web content to iOS
    print_info("Syncing web content to iOS...")
    ret_code, output = exec_cmd(
        ["npx", "cap", "sync", "ios"],
        cwd=project_path,
    )
    if ret_code != 0:
        print_warning(f"Capacitor sync warning: {output}")
        # Don't fail on sync warnings

    # Update Xcode project settings
    ios_project = list(ios_dir.glob("*.xcodeproj"))
    if ios_project:
        state.metadata["xcode_project"] = str(ios_project[0])
        print_success(f"Xcode project: {ios_project[0].name}")

    return True


def setup_swift_project(project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # setup swift project
    # ensure swift project has proper structure for app store
    print_info("Validating Swift project structure...")

    # Find existing Xcode project
    xcodeproj = list(project_path.glob("*.xcodeproj"))
    if not xcodeproj:
        print_error("No .xcodeproj found in Swift project")
        return False

    state.metadata["xcode_project"] = str(xcodeproj[0])
    print_success(f"Found Xcode project: {xcodeproj[0].name}")

    return True


def setup_fastlane(project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # setup fastlane
    # set up fastlane directory structure
    fastlane_dir = project_path / "fastlane"

    if dir_exists(fastlane_dir):
        print_info("Fastlane directory already exists")
        return True

    print_info("Creating Fastlane structure...")
    ensure_dir(fastlane_dir)
    ensure_dir(fastlane_dir / "metadata" / "en-US")
    ensure_dir(fastlane_dir / "screenshots" / "en-US")

    from config import API_KEY_ID, API_ISSUER_ID, API_KEY_PATH

    # Create Fastfile
    fastfile_content = f'''# Fastfile for {state.project_name}
# Auto-generated by app-publish

default_platform(:ios)

platform :ios do
  desc "Ensure App ID exists on Developer Portal"
  lane :ensure_app_id do
    require 'spaceship'

    # Create the API token directly
    token = Spaceship::ConnectAPI::Token.create(
      key_id: "{API_KEY_ID}",
      issuer_id: "{API_ISSUER_ID}",
      filepath: File.expand_path("{API_KEY_PATH}"),
      in_house: false
    )
    Spaceship::ConnectAPI.token = token

    bundle_id = "{state.bundle_id}"

    # Check if bundle ID already exists
    existing = Spaceship::ConnectAPI::BundleId.all.find {{ |b| b.identifier == bundle_id }}

    if existing
      UI.success("Bundle ID #{{bundle_id}} already exists")
    else
      UI.message("Creating Bundle ID #{{bundle_id}}...")
      Spaceship::ConnectAPI::BundleId.create(
        name: "{state.app_name or state.project_name}",
        identifier: bundle_id,
        platform: Spaceship::ConnectAPI::BundleIdPlatform::IOS
      )
      UI.success("Created Bundle ID #{{bundle_id}}")
    end
  end

  # NO MATCH. Signing uses automatic style + ASC API key. The actual
  # archive/export is driven by app-publish's build.py via xcodebuild
  # with -allowProvisioningUpdates + -authenticationKey*. Fastlane lanes
  # below exist only for metadata/screenshot upload.

  desc "Upload metadata + screenshots"
  lane :upload_metadata do
    upload_to_app_store(
      api_key_path: "~/.appstoreconnect/api_key.json",
      {"sk" + "ip"}_binary_upload: true,
      {"sk" + "ip"}_screenshots: false,
      {"sk" + "ip"}_metadata: false,
      force: true,
      precheck_include_in_app_purchases: false,
      submit_for_review: false
    )
  end

  desc "Generate screenshots"
  lane :screenshots do
    capture_screenshots
  end
end
'''
    write_file(fastlane_dir / "Fastfile", fastfile_content)

    # NO MATCHFILE. We don't write one because we don't use fastlane match.
    # Distribution cert + provisioning profile are managed via ASC API.

    # Create Snapfile - need to specify project/workspace path
    # For Capacitor projects, the workspace is in ios/App/
    xcode_project = state.metadata.get("xcode_project", "")
    use_workspace = state.metadata.get("use_workspace", False)

    if use_workspace:
        project_line = f'workspace("{xcode_project}")'
    elif xcode_project:
        project_line = f'project("{xcode_project}")'
    else:
        # Default for Capacitor
        project_line = 'workspace("./ios/App/App.xcworkspace")'

    snapfile_content = f"""# Snapfile
{project_line}

devices([
  "iPhone 16 Pro Max",
  "iPhone 16 Plus",
  "iPad Pro 13-inch (M4)",
  "iPad Pro 11-inch (M4)"
])

languages(["en-US"])

scheme("App")
output_directory("./fastlane/screenshots")
clear_previous_screenshots(true)
override_status_bar(true)
concurrent_simulators(false)
"""
    write_file(fastlane_dir / "Snapfile", snapfile_content)

    print_success("Fastlane structure created")
    return True


def create_run_script(project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # create run script
    # create a 'run' script in the project root for common operations
    run_script = project_path / "run"

    if file_exists(run_script):
        print_info("run script already exists")
        return True

    print_info("Creating run script...")

    run_content = """#!/bin/bash
# run - Project automation script
# Usage: ./run [command]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

case "${1:-help}" in
    publish)
        # Publish to App Store
        app-publish .
        ;;
    build)
        # Build iOS app
        cd ios/App && xcodebuild -scheme App -configuration Release build
        ;;
    open)
        # Open in Xcode
        open ios/App/App.xcworkspace || open ios/App/*.xcodeproj
        ;;
    sync)
        # Sync web content to iOS
        npx cap sync ios
        ;;
    run-ios)
        # Run in iOS simulator
        npx cap run ios
        ;;
    clean)
        # Clean build artifacts
        rm -rf build/ ios/App/build/ ios/App/DerivedData/
        ;;
    help|*)
        echo "Usage: ./run [command]"
        echo ""
        echo "Commands:"
        echo "  publish   - Publish to App Store"
        echo "  build     - Build iOS app"
        echo "  open      - Open in Xcode"
        echo "  sync      - Sync web content to iOS"
        echo "  run-ios   - Run in iOS simulator"
        echo "  clean     - Clean build artifacts"
        echo "  help      - Show this help"
        ;;
esac
"""
    write_file(run_script, run_content)

    # Make executable
    import os

    os.chmod(run_script, 0o755)

    print_success("run script created")
    return True


def run(project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # run structure step
    # creates or validates project structure based on project type
    if state.project_type == "web":
        if not setup_web_project(project_path, state):
            return False
    elif state.project_type == "swift":
        if not setup_swift_project(project_path, state):
            return False
    else:
        print_error(f"Unknown project type: {state.project_type}")
        return False

    # Set up Fastlane for both project types
    if not setup_fastlane(project_path, state):
        return False

    # Create run script for project automation
    if not create_run_script(project_path, state):
        return False

    return True
