#!/usr/bin/env python3
# interactive screenshot capture for app store
# guides user through manual screenshot capture on the simulator since
# automated click simulation is unreliable across different macos versions
from threading import Event
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    ensure_dir,
)
from state import load_state, save_state
from modules.screenshots import analyze_screenshot_scenarios

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
# screenshot output path
# construct the stable App Store filename for a device and scenario
def screenshot_output_path(device: dict, output_dir: Path, scenario_name: str) -> Path:
    return output_dir / f"{device['prefix']}-{scenario_name}.png"


# ##################################################################
# select screenshot devices
# parse an interactive selection into the configured device records
def select_screenshot_devices(choice: str) -> list[dict]:
    if choice.strip().lower() == "all":
        return DEVICES
    try:
        indices = [int(value.strip()) - 1 for value in choice.split(",")]
    except ValueError:
        return DEVICES[:1]
    selected = [DEVICES[index] for index in indices if 0 <= index < len(DEVICES)]
    return selected or DEVICES[:1]


# ##################################################################
# capture screenshot
# capture screenshot from simulator to output path
def capture_screenshot(device_name: str, output_path: Path) -> bool:
    ret_code, _ = exec_cmd(["xcrun", "simctl", "io", device_name, "screenshot", str(output_path)])
    return ret_code == 0


# ##################################################################
# wait for enter
# wait for user to press enter with a message prompt
def wait_for_enter(message: str) -> None:
    print_info(f"\n>>> {message}")
    print_info("    Press ENTER when ready...")
    input()


# ##################################################################
# build device capture plan
# describe every local command, delay, prompt, and output before execution
def build_device_capture_plan(device: dict, output_dir: Path, bundle_id: str, screenshots: list[dict]) -> list[tuple]:
    device_name = device["name"]
    plan = [
        ("info", f"\n{'=' * 60}"),
        ("info", f"CAPTURING SCREENSHOTS FOR: {device_name}"),
        ("info", f"{'=' * 60}"),
        ("info", "\nBooting simulator..."),
        ("command", ["xcrun", "simctl", "boot", device_name]),
        ("wait", 3),
        ("command", ["open", "-a", "Simulator"]),
        ("wait", 2),
        ("info", "Launching app fresh..."),
        ("command", ["xcrun", "simctl", "terminate", device_name, bundle_id]),
        ("wait", 1),
        ("command", ["xcrun", "simctl", "launch", device_name, bundle_id]),
        ("wait", 3),
        ("info", "The app should now be visible in the Simulator window."),
        ("info", "Follow the prompts below to capture each screenshot."),
    ]
    for scenario in screenshots:
        filepath = screenshot_output_path(device, output_dir, scenario["name"])
        navigation = scenario.get("navigation", "")
        navigation_hint = f"\n    Navigation: {navigation}" if navigation else ""
        prompt = (
            f"Navigate to: {scenario['description']}{navigation_hint}\n"
            f"    Then press ENTER to capture '{filepath.name}'"
        )
        plan.append(("capture", device_name, filepath, prompt))
    plan.extend(
        [
            ("info", "\nShutting down simulator..."),
            ("command", ["xcrun", "simctl", "shutdown", device_name]),
            ("wait", 2),
        ]
    )
    return plan


# ##################################################################
# execute capture plan
# run a precomputed local simulator plan and count successful captures
def execute_capture_plan(plan: list[tuple]) -> int:
    captured = 0
    for action in plan:
        if action[0] == "info":
            print_info(action[1])
        elif action[0] == "command":
            exec_cmd(action[1])
        elif action[0] == "wait":
            Event().wait(action[1])
        elif action[0] == "capture":
            _, device_name, filepath, prompt = action
            wait_for_enter(prompt)
            if capture_screenshot(device_name, filepath):
                print_success(f"    Captured: {filepath.name}")
                captured += 1
            else:
                print_error(f"    Failed to capture: {filepath.name}")
    return captured


# ##################################################################
# capture device screenshots
# capture all screenshots for one device with user guidance
def capture_device_screenshots(device: dict, output_dir: Path, bundle_id: str, screenshots: list[dict]) -> int:
    return execute_capture_plan(build_device_capture_plan(device, output_dir, bundle_id, screenshots))


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

    # Analyze app to determine screenshot scenarios (cached in state)
    print_info("Determining screenshot scenarios for this app...")
    scenarios = analyze_screenshot_scenarios(project_path, state)
    save_state(project_path, state)  # Save cached scenarios

    print_info(f"\nScreenshots to capture ({len(scenarios)} scenarios):")
    for i, scenario in enumerate(scenarios, 1):
        print_info(f"  {i}. {scenario['name']}: {scenario['description']}")

    # remove old screenshots
    print_info("\nRemoving old screenshots...")
    for f in output_dir.glob("*.png"):
        f.unlink()

    print_info(f"\nOutput directory: {output_dir}")

    # shutdown any running simulators first
    print_info("Shutting down all simulators...")
    exec_cmd(["xcrun", "simctl", "shutdown", "all"])
    Event().wait(2)

    total_captured = 0

    # ask which devices to capture
    print_info("\nAvailable devices:")
    for i, device in enumerate(DEVICES):
        print_info(f"  {i + 1}. {device['name']}")

    print_info("\nWhich devices do you want to capture? (comma-separated numbers, or 'all')")
    print_info("Example: 1,2 for iPhone 16 Pro Max and iPhone 16 Plus")
    choice = input("Enter choice: ").strip()

    selected_devices = select_screenshot_devices(choice)

    for device in selected_devices:
        try:
            count = capture_device_screenshots(device, output_dir, bundle_id, scenarios)
            total_captured += count
        except KeyboardInterrupt:
            print_warning("\nCapture interrupted by user")
            break
        except Exception as e:
            print_error(f"Error with {device['name']}: {e}")
            continue

    print_info(f"\n{'=' * 60}")
    print_success(f"Total captured: {total_captured} screenshots")
    print_info(f"{'=' * 60}")

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
