#!/usr/bin/env python3
# simple screenshot capture helper
# usage:
#     python capture.py <project_path> [screen_name|all]
#     python capture.py /path/to/project 01_main    - capture specific screen
#     python capture.py /path/to/project all        - interactive capture of all screens
#     python capture.py /path/to/project            - list available screens
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from state import load_state, save_state
from modules.screenshots import analyze_screenshot_scenarios

DEVICE = "iPhone 16 Pro Max"


# ##################################################################
# get screens from state
# load screenshot scenarios from project state (or analyze if not cached)
def get_screens(project_path: Path) -> dict[str, dict]:
    state = load_state(project_path)
    scenarios = analyze_screenshot_scenarios(project_path, state)
    save_state(project_path, state)  # Cache for future runs

    # Convert to dict keyed by name for easy lookup
    return {s["name"]: s for s in scenarios}


# ##################################################################
# capture
# capture a screenshot from the simulator for the named screen
def capture(project_path: Path, name: str, screens: dict) -> bool:
    if name not in screens:
        print(f"Unknown screen: {name}")
        print(f"Available: {', '.join(screens.keys())}")
        return False

    output_dir = project_path / "fastlane" / "screenshots" / "en-US"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{DEVICE}-{name}.png"
    filepath = output_dir / filename

    result = subprocess.run(
        ["xcrun", "simctl", "io", DEVICE, "screenshot", str(filepath)],
        capture_output=True, text=True
    )

    if result.returncode == 0:
        print(f"Captured: {filename}")
        return True
    else:
        print(f"Failed: {result.stderr}")
        return False


# ##################################################################
# interactive all
# interactively capture all screenshots by prompting user to navigate
def interactive_all(project_path: Path, screens: dict) -> None:
    output_dir = project_path / "fastlane" / "screenshots" / "en-US"
    print("\n=== Interactive Screenshot Capture ===")
    print("Navigate to each screen in Simulator, then press Enter.\n")

    for name, scenario in screens.items():
        description = scenario.get("description", name)
        navigation = scenario.get("navigation", "")
        nav_hint = f" ({navigation})" if navigation else ""
        input(f"Navigate to: {description}{nav_hint}\n  Press Enter to capture '{name}': ")
        capture(project_path, name, screens)

    print(f"\nDone! Screenshots saved to: {output_dir}")


# ##################################################################
# main
# parse command line and dispatch to appropriate capture function
def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python capture.py <project_path> [screen_name|all]")
        print("  project_path: Path to the iOS project")
        print("  screen_name: Name of specific screen to capture (optional)")
        print("  all: Interactively capture all screens")
        return 1

    project_path = Path(sys.argv[1]).resolve()
    if not project_path.exists():
        print(f"Project path does not exist: {project_path}")
        return 1

    screens = get_screens(project_path)

    if len(sys.argv) < 3:
        # List available screens
        print(f"Available screens for {project_path.name}:")
        for name, scenario in screens.items():
            print(f"  {name}: {scenario.get('description', '')}")
        print("\nUsage: python capture.py <project_path> [screen_name|all]")
        return 0

    cmd = sys.argv[2]

    if cmd == "all":
        interactive_all(project_path, screens)
        return 0
    elif cmd in screens:
        return 0 if capture(project_path, cmd, screens) else 1
    else:
        print(f"Unknown screen: {cmd}")
        print(f"Available: {', '.join(screens.keys())}")
        return 1


# ##################################################################
# entry point
# standard python pattern for dispatching main
if __name__ == "__main__":
    sys.exit(main())
