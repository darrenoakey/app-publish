#!/usr/bin/env python3
# simple screenshot capture helper
# usage:
#     python capture.py title    - capture title screen
#     python capture.py setup    - capture setup screen
#     python capture.py bidding  - capture bidding screen
#     python capture.py play     - capture play screen
#     python capture.py trick    - capture trick screen
#     python capture.py all      - interactive capture of all screens
import sys
import subprocess
from pathlib import Path

DEVICE = "iPhone 16 Pro Max"
OUTPUT_DIR = Path("./fastlane/screenshots/en-US")

SCREENS = {
    "title": "01_title",
    "setup": "02_setup",
    "bidding": "03_bidding",
    "play": "04_play",
    "trick": "05_trick",
}


# ##################################################################
# capture
# capture a screenshot from the simulator for the named screen
def capture(name: str) -> bool:
    if name not in SCREENS:
        print(f"Unknown screen: {name}")
        print(f"Available: {', '.join(SCREENS.keys())}")
        return False

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{DEVICE}-{SCREENS[name]}.png"
    filepath = OUTPUT_DIR / filename

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
def interactive_all() -> None:
    print("\n=== Interactive Screenshot Capture ===")
    print("Navigate to each screen in Simulator, then press Enter.\n")

    for name, prefix in SCREENS.items():
        input(f"Navigate to {name.upper()} screen, then press Enter: ")
        capture(name)

    print("\nDone! Screenshots saved to:", OUTPUT_DIR)


# ##################################################################
# main
# parse command line and dispatch to appropriate capture function
def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python capture.py [title|setup|bidding|play|trick|all]")
        return 1

    cmd = sys.argv[1].lower()

    if cmd == "all":
        interactive_all()
        return 0
    elif cmd in SCREENS:
        return 0 if capture(cmd) else 1
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python capture.py [title|setup|bidding|play|trick|all]")
        return 1


# ##################################################################
# entry point
# standard python pattern for dispatching main
if __name__ == "__main__":
    sys.exit(main())
