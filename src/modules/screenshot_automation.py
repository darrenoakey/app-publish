import json
import time
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    ensure_dir,
)


# Screenshot scenarios for a Bridge card game app
BRIDGE_SCENARIOS = [
    {
        "name": "01_title",
        "description": "Title screen with game logo",
        "js_setup": None,  # Already at title on launch
        "wait": 1,
        "priority": 1
    },
    {
        "name": "02_game_start",
        "description": "Game selector with seed controls",
        "js_setup": "window.startGame && window.startGame()",
        "wait": 2,
        "priority": 1
    },
    {
        "name": "03_bidding",
        "description": "Bidding phase with auction table",
        "js_setup": None,  # Auto-progresses from start
        "wait": 3,
        "priority": 1
    },
    {
        "name": "04_gameplay",
        "description": "Card play with cards on table",
        "js_setup": None,  # Need to complete bidding first
        "wait": 3,
        "priority": 1
    },
    {
        "name": "05_results",
        "description": "Hand results screen",
        "js_setup": None,
        "wait": 2,
        "priority": 1
    },
]

# Required device screenshots
DEVICES = [
    {"name": "iPhone 16 Pro Max", "suffix": "iphone67", "size": "6.7 inch"},
    {"name": "iPhone 16 Plus", "suffix": "iphone65", "size": "6.5 inch"},
    {"name": "iPad Pro 13-inch (M4)", "suffix": "ipad129", "size": "12.9 inch"},
    {"name": "iPad Pro 11-inch (M4)", "suffix": "ipad11", "size": "11 inch"},
]


# ##################################################################
# boot simulator
# boot a simulator by name
def boot_simulator(device_name: str) -> bool:
    ret_code, _ = exec_cmd(["xcrun", "simctl", "boot", device_name])
    if ret_code != 0:
        # Already booted is OK
        pass
    time.sleep(2)
    return True


# ##################################################################
# install app
# install app on simulator
def install_app(device_name: str, app_path: Path) -> bool:
    ret_code, output = exec_cmd([
        "xcrun", "simctl", "install", device_name, str(app_path)
    ])
    return ret_code == 0


# ##################################################################
# launch app
# launch app on simulator
def launch_app(device_name: str, bundle_id: str) -> bool:
    ret_code, output = exec_cmd([
        "xcrun", "simctl", "launch", device_name, bundle_id
    ])
    return ret_code == 0


# ##################################################################
# capture screenshot
# capture screenshot from simulator
def capture_screenshot(device_name: str, output_path: Path) -> bool:
    ret_code, output = exec_cmd([
        "xcrun", "simctl", "io", device_name, "screenshot", str(output_path)
    ])
    return ret_code == 0


# ##################################################################
# inject javascript
# inject javascript into running app
# note: this is complex for capacitor apps, alternative approaches include
# using safari web inspector programmatically, adding a debug endpoint to the app,
# or using accessibility framework
def inject_javascript(device_name: str, bundle_id: str, js_code: str) -> bool:
    # For now, we'll use a simpler approach - capture what we can
    print_warning(f"JavaScript injection not directly supported: {js_code[:50]}...")
    return True


# ##################################################################
# click simulator
# simulate click in simulator using applescript to click in the simulator window
def click_simulator(device_name: str, x: int, y: int) -> bool:
    script = f'''
    tell application "Simulator"
        activate
    end tell
    delay 0.5
    tell application "System Events"
        tell process "Simulator"
            set frontmost to true
            delay 0.3
            click at {{{x}, {y}}}
        end tell
    end tell
    '''
    ret_code, output = exec_cmd(["osascript", "-e", script])
    return ret_code == 0


# ##################################################################
# capture title screen
# capture title screen for a device
def capture_title_screen(device: dict, screenshot_dir: Path, bundle_id: str, app_path: Path) -> bool:
    device_name = device["name"]
    suffix = device["suffix"]

    print_info(f"Capturing title screen on {device_name}...")

    # Boot, install, launch
    boot_simulator(device_name)
    install_app(device_name, app_path)
    launch_app(device_name, bundle_id)
    time.sleep(3)  # Wait for app to fully load

    # Capture
    output_path = screenshot_dir / f"01_title_{suffix}.png"
    if capture_screenshot(device_name, output_path):
        print_success(f"Captured: {output_path.name}")
        return True
    else:
        print_error(f"Failed to capture: {output_path.name}")
        return False


# ##################################################################
# capture all devices
# capture screenshots on all required devices
def capture_all_devices(project_path: Path, bundle_id: str, app_path: Path) -> int:
    screenshot_dir = project_path / "fastlane" / "screenshots" / "en-US"
    ensure_dir(screenshot_dir)

    captured = 0

    for device in DEVICES:
        if capture_title_screen(device, screenshot_dir, bundle_id, app_path):
            captured += 1

    return captured


# ##################################################################
# run screenshot capture
# run screenshot capture with project_path (path to project root),
# bundle_id (app bundle identifier), and optional app_path (path to .app bundle for simulator)
def run(project_path: Path, bundle_id: str, app_path: Path = None) -> bool:
    screenshot_dir = project_path / "fastlane" / "screenshots" / "en-US"
    ensure_dir(screenshot_dir)

    # Find app if not provided
    if not app_path:
        # Try common locations
        sim_build = Path("/tmp/app-sim-build/Build/Products/Debug-iphonesimulator/App.app")
        if sim_build.exists():
            app_path = sim_build
        else:
            print_error("No simulator app found. Build for simulator first.")
            return False

    print_info("Capturing App Store screenshots...")
    print_info(f"Output: {screenshot_dir}")

    # Open Simulator app
    exec_cmd(["open", "-a", "Simulator"])
    time.sleep(1)

    # Capture on all devices
    captured = capture_all_devices(project_path, bundle_id, app_path)

    print_success(f"Captured {captured} screenshots")

    if captured < len(DEVICES):
        print_warning("Some screenshots failed. Check simulator setup.")

    # List what was captured
    screenshots = list(screenshot_dir.glob("*.png"))
    if screenshots:
        print_info(f"Screenshots in {screenshot_dir}:")
        for s in sorted(screenshots):
            print_info(f"  - {s.name}")

    return captured > 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python screenshot_automation.py <project_path> [bundle_id] [app_path]")
        sys.exit(1)

    project_path = Path(sys.argv[1]).resolve()
    if len(sys.argv) > 2:
        bundle_id = sys.argv[2]
    else:
        print_error("Bundle ID is required")
        sys.exit(1)
        
    app_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    success = run(project_path, bundle_id, app_path)
    if success:
        print_success("Screenshot capture completed!")
    else:
        print_error("Screenshot capture failed!")
        sys.exit(1)
