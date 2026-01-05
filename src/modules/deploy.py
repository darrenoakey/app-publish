# deploy module - installs app on connected ios device
#
# handles:
# - building a debug/development ipa
# - finding connected ios devices
# - installing the app on the device

import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import TEAM_ID
from state import ProjectState, load_state
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
)


# ##################################################################
# find connected devices
# finds all connected ios devices
def find_connected_devices() -> list[dict]:
    devices = []

    # Try xctrace first (most reliable)
    ret_code, output = exec_cmd(
        ["xcrun", "xctrace", "list", "devices"],
        timeout=30
    )

    if ret_code == 0:
        import re
        # Parse lines like: "Starbuck (26.3) (00008150-000611360AC0401C)"
        for line in output.split("\n"):
            # Match real devices (not simulators)
            match = re.match(r'^(.+?) \((\d+\.\d+)\) \(([A-F0-9-]+)\)$', line.strip())
            if match and "Simulator" not in line:
                name, os_ver, device_id = match.groups()
                # Skip Macs
                if "MacBook" not in name and "Mac" not in name:
                    devices.append({
                        "id": device_id,
                        "name": name,
                        "model": "iOS Device",
                        "os": os_ver,
                    })

    # Fallback to devicectl
    if not devices:
        ret_code, output = exec_cmd(
            ["xcrun", "devicectl", "list", "devices", "--json-output", "/dev/stdout"],
            timeout=30
        )

        if ret_code == 0:
            import json
            try:
                data = json.loads(output)
                for device in data.get("result", {}).get("devices", []):
                    if device.get("connectionProperties", {}).get("transportType") == "wired":
                        devices.append({
                            "id": device.get("identifier"),
                            "name": device.get("deviceProperties", {}).get("name"),
                            "model": device.get("deviceProperties", {}).get("marketingName"),
                            "os": device.get("deviceProperties", {}).get("osVersionNumber"),
                        })
            except json.JSONDecodeError:
                pass

    return devices
# ##################################################################
# find connected devices
# finds all connected ios devices


# ##################################################################
# build for device
# builds the app for a real device using development signing
def build_for_device(project_path: Path, state: ProjectState) -> str | None:
    ios_path = project_path / "ios" / "App"

    if not ios_path.exists():
        print_error("iOS project not found")
        return None

    # Find the xcodeproj
    xcodeproj = ios_path / "App.xcodeproj"
    if not xcodeproj.exists():
        print_error(f"Xcode project not found: {xcodeproj}")
        return None

    # Build for device
    print_info("Building for device (Debug configuration)...")

    build_dir = project_path / "build" / "device"
    build_dir.mkdir(parents=True, exist_ok=True)

    ret_code, output = exec_cmd(
        [
            "xcodebuild",
            "-project", str(xcodeproj),
            "-scheme", "App",
            "-configuration", "Debug",
            "-destination", "generic/platform=iOS",
            "-derivedDataPath", str(build_dir),
            f"DEVELOPMENT_TEAM={TEAM_ID}",
            "CODE_SIGN_IDENTITY=Apple Development",
            "build",
        ],
        cwd=project_path,
        timeout=300
    )

    if ret_code != 0:
        print_error("Build failed")
        return None

    # Find the app bundle
    app_path = build_dir / "Build" / "Products" / "Debug-iphoneos" / "App.app"
    if app_path.exists():
        return str(app_path)

    # Search for it
    for app in build_dir.rglob("*.app"):
        if "Debug-iphoneos" in str(app):
            return str(app)

    print_error("Could not find built app bundle")
    return None
# ##################################################################
# build for device
# builds the app for a real device using development signing


# ##################################################################
# install on device
# installs app on connected device
def install_on_device(app_path: str, device_id: str | None = None) -> bool:
    print_info(f"Installing {Path(app_path).name}...")

    # Try devicectl first (Xcode 15+)
    cmd = ["xcrun", "devicectl", "device", "install", "app"]
    if device_id:
        cmd.extend(["--device", device_id])
    cmd.append(app_path)

    ret_code, output = exec_cmd(cmd, timeout=120)

    if ret_code == 0:
        return True

    # Fallback to ios-deploy
    print_info("Trying ios-deploy...")
    cmd = ["ios-deploy", "--bundle", app_path]
    if device_id:
        cmd.extend(["--id", device_id])

    ret_code, output = exec_cmd(cmd, timeout=120)

    if ret_code == 0:
        return True

    print_error("Installation failed")
    print_info("Make sure device is unlocked and trusts this computer")
    return False
# ##################################################################
# install on device
# installs app on connected device


# ##################################################################
# run
# deploys app to connected ios device
# device_name: name of device to deploy to (default: "Starbuck")
def run(project_path: Path, state: ProjectState, device_name: str = "Starbuck") -> bool:
    print_info(f"Looking for device: {device_name}")
    print_info("Checking for connected iOS devices...")

    devices = find_connected_devices()

    if not devices:
        print_error("No iOS devices connected")
        print_info("Connect your iPhone via USB and trust this computer")
        return False

    # Show connected devices
    print_success(f"Found {len(devices)} device(s):")
    for d in devices:
        print_info(f"  - {d['name']} ({d['model']}) - {d['id'][:12]}...")

    # Find matching device by name
    device = None
    for d in devices:
        if device_name.lower() in d["name"].lower():
            device = d
            break

    if not device:
        print_warning(f"Device '{device_name}' not found, using first available")
        device = devices[0]

    device_id = device["id"]
    print_info(f"Deploying to: {device['name']}")

    # Build for device
    app_path = build_for_device(project_path, state)
    if not app_path:
        return False

    print_success(f"Built: {app_path}")

    # Install
    if not install_on_device(app_path, device_id):
        return False

    print_success(f"Deployed to {device['name']}!")
    return True
# ##################################################################
# run
# deploys app to connected ios device
# device_name: name of device to deploy to (default: "Starbuck")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python deploy.py <project_path> [device_name]")
        print("       device_name defaults to 'Starbuck'")
        sys.exit(1)

    project_path = Path(sys.argv[1]).resolve()
    device_name = sys.argv[2] if len(sys.argv) > 2 else "Starbuck"
    state = load_state(project_path)

    success = run(project_path, state, device_name)
    if success:
        print_success("Deploy completed!")
    else:
        print_error("Deploy failed!")
        sys.exit(1)
