#!/usr/bin/env python3
# interactive screenshot capture for app store
# guides user through manual screenshot capture on the simulator since
# automated click simulation is unreliable across different macos versions
import time
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from utils import print_info, print_success, print_warning, print_error, run as exec_cmd, ensure_dir
from state import load_state

# screenshots needed for app store
SCREENSHOTS = [
    ("01_title", "Title screen - shows 'CLICK TO START'"),
    ("02_setup", "Setup screen - shows seed/deal options and PLAY button"),
    ("03_bidding", "Bidding screen - shows bidding UI with cards visible"),
    ("04_play", "Play screen - shows cards on table during play"),
    ("05_trick", "Trick result - shows completed trick with cards"),
]

# device configurations
DEVICES = [
    {
        "name": "iPhone 16 Pro Max",
        "prefix": "iPhone 16 Pro Max",
    },
    {
        "name": "iPhone 16 Plus",
        "prefix": "iPhone 16 Plus",
    },
    {
        "name": "iPad Pro 13-inch (M4)",
        "prefix": "iPad Pro 13-inch (M4)",
    },
    {
        "name": "iPad Pro 11-inch (M4)",
        "prefix": "iPad Pro 11-inch (M4)",
    },
]


# ##################################################################
# capture screenshot
# capture screenshot from simulator to output path
def capture_screenshot(device_name: str, output_path: Path) -> bool:
    ret_code, _ = exec_cmd([
        "xcrun", "simctl", "io", device_name, "screenshot", str(output_path)
    ])
    return ret_code == 0


# ##################################################################
# wait for enter
# wait for user to press enter with a message prompt
def wait_for_enter(message: str) -> None:
    print_info(f"\n>>> {message}")
    print_info("    Press ENTER when ready...")
    input()


# ##################################################################
# capture device screenshots
# capture all screenshots for one device with user guidance
def capture_device_screenshots(device: dict, output_dir: Path, bundle_id: str) -> int:
    device_name = device["name"]
    prefix = device["prefix"]
    captured = 0

    print_info(f"\n{'='*60}")
    print_info(f"CAPTURING SCREENSHOTS FOR: {device_name}")
    print_info(f"{'='*60}")

    # boot simulator
    print_info("\nBooting simulator...")
    exec_cmd(["xcrun", "simctl", "boot", device_name])
    time.sleep(3)
    exec_cmd(["open", "-a", "Simulator"])
    time.sleep(2)

    # terminate and relaunch app
    print_info("Launching app fresh...")
    exec_cmd(["xcrun", "simctl", "terminate", device_name, bundle_id])
    time.sleep(1)
    exec_cmd(["xcrun", "simctl", "launch", device_name, bundle_id])
    time.sleep(3)

    print_info("\n" + "="*60)
    print_info("The app should now be visible in the Simulator window.")
    print_info("Follow the prompts below to capture each screenshot.")
    print_info("="*60)

    for screenshot_name, description in SCREENSHOTS:
        filename = f"{prefix}-{screenshot_name}.png"
        filepath = output_dir / filename

        wait_for_enter(f"Navigate to: {description}\n    Then press ENTER to capture '{filename}'")

        if capture_screenshot(device_name, filepath):
            print_success(f"    Captured: {filename}")
            captured += 1
        else:
            print_error(f"    Failed to capture: {filename}")

    # shutdown simulator
    print_info("\nShutting down simulator...")
    exec_cmd(["xcrun", "simctl", "shutdown", device_name])
    time.sleep(2)

    return captured


# ##################################################################
# main
# entry point that parses args and orchestrates screenshot capture
def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python manual_screenshots.py <project_path>")
        return 1

    project_path = Path(sys.argv[1]).resolve()
    state = load_state(project_path)

    if not state.bundle_id:
        print_error("Bundle ID not found in project state. Run structure step first.")
        return 1

    bundle_id = state.bundle_id
    output_dir = project_path / "fastlane" / "screenshots" / "en-US"
    ensure_dir(output_dir)

    # remove old screenshots
    print_info("Removing old screenshots...")
    for f in output_dir.glob("*.png"):
        f.unlink()

    print_info(f"\nOutput directory: {output_dir}")

    # shutdown any running simulators first
    print_info("Shutting down all simulators...")
    exec_cmd(["xcrun", "simctl", "shutdown", "all"])
    time.sleep(2)

    total_captured = 0

    # ask which devices to capture
    print_info("\nAvailable devices:")
    for i, device in enumerate(DEVICES):
        print_info(f"  {i+1}. {device['name']}")

    print_info("\nWhich devices do you want to capture? (comma-separated numbers, or 'all')")
    print_info("Example: 1,2 for iPhone 16 Pro Max and iPhone 16 Plus")
    choice = input("Enter choice: ").strip()

    if choice.lower() == 'all':
        selected_devices = DEVICES
    else:
        try:
            indices = [int(x.strip()) - 1 for x in choice.split(',')]
            selected_devices = [DEVICES[i] for i in indices if 0 <= i < len(DEVICES)]
        except (ValueError, IndexError):
            print_error("Invalid choice. Using iPhone 16 Pro Max only.")
            selected_devices = DEVICES[:1]

    for device in selected_devices:
        try:
            count = capture_device_screenshots(device, output_dir, bundle_id)
            total_captured += count
        except KeyboardInterrupt:
            print_warning("\nCapture interrupted by user")
            break
        except Exception as e:
            print_error(f"Error with {device['name']}: {e}")
            continue

    print_info(f"\n{'='*60}")
    print_success(f"Total captured: {total_captured} screenshots")
    print_info(f"{'='*60}")

    # list what was captured
    screenshots = sorted(output_dir.glob("*.png"))
    if screenshots:
        print_info("\nScreenshots captured:")
        for s in screenshots:
            print_info(f"  - {s.name}")
    else:
        print_warning("No screenshots were captured!")

    return 0


# ##################################################################
# entry point
# standard python pattern for dispatching main
if __name__ == "__main__":
    sys.exit(main())
