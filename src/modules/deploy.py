# deploy module - installs app on connected ios device
#
# handles:
# - ensuring development provisioning profile
# - building a debug/development ipa
# - finding connected ios devices
# - installing the app on the device

import re
import json
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
    parse_schemes,
    pick_scheme,
)


# ##################################################################
# detect bundle id
# auto-detect bundle id from capacitor.config.json or project.yml
def detect_bundle_id(project_path: Path) -> str | None:
    # try capacitor.config.json
    cap_config = project_path / "capacitor.config.json"
    if cap_config.exists():
        try:
            data = json.loads(cap_config.read_text())
            bid = data.get("appId")
            if bid:
                return bid
        except json.JSONDecodeError:
            pass

    # try project.yml (XcodeGen)
    project_yml = project_path / "project.yml"
    if project_yml.exists():
        text = project_yml.read_text()
        match = re.search(r'PRODUCT_BUNDLE_IDENTIFIER:\s*["\']?([^\s"\']+)', text)
        if match:
            return match.group(1)

    # try finding xcodeproj and reading build settings
    for xcodeproj in project_path.glob("*.xcodeproj"):
        ret_code, output = exec_cmd(
            ["xcodebuild", "-project", str(xcodeproj), "-showBuildSettings"],
            timeout=30,
        )
        if ret_code == 0:
            match = re.search(r"PRODUCT_BUNDLE_IDENTIFIER = (.+)", output)
            if match:
                return match.group(1).strip()

    return None


# ##################################################################
# detect project type
# determine if project is capacitor (web) or native
def detect_project_type(project_path: Path) -> tuple[str, Path, str]:
    """Returns (project_type, xcodeproj_path, scheme_name)."""
    # capacitor project
    cap_xcodeproj = project_path / "ios" / "App" / "App.xcodeproj"
    if cap_xcodeproj.exists():
        return "web", cap_xcodeproj, "App"

    # native project - find xcodeproj at root
    for xcodeproj in sorted(project_path.glob("*.xcodeproj")):
        # get scheme from xcodebuild -list
        ret_code, output = exec_cmd(
            ["xcodebuild", "-project", str(xcodeproj), "-list"],
            timeout=30,
        )
        if ret_code == 0:
            schemes = parse_schemes(output)
            if schemes:
                return "native", xcodeproj, pick_scheme(schemes, xcodeproj.stem)

        # alternative: use project name as scheme
        return "native", xcodeproj, xcodeproj.stem

    return "unknown", Path(), ""


# ##################################################################
# ensure dev profile
# NEVER USE FASTLANE MATCH. Dev provisioning is handled by Xcode's automatic
# signing during xcodebuild via -allowProvisioningUpdates. This function is a
# no-op kept only for callers that still invoke it.
def ensure_dev_profile(bundle_id: str) -> bool:
    print_info(f"Using automatic signing for {bundle_id} (no fastlane match)")
    return True


# ##################################################################
# sync web content
# syncs www/ to ios/App/App/public/ using capacitor
# this MUST be called before building to ensure latest changes are deployed
def sync_web_content(project_path: Path) -> bool:
    print_info("Syncing web content to iOS...")
    ret_code, output = exec_cmd(
        ["npx", "cap", "sync", "ios"],
        cwd=project_path,
    )
    if ret_code != 0:
        print_warning(f"Capacitor sync warning: {output}")
        # don't fail on warnings
    return True


def parse_xctrace_devices(output: str) -> list[dict]:
    devices = []
    for line in output.splitlines():
        match = re.match(r"^(.+?) \((\d+\.\d+)\) \(([A-F0-9-]+)\)$", line.strip())
        if match and "Simulator" not in line:
            name, os_ver, device_id = match.groups()
            if "MacBook" not in name and "Mac" not in name and "Watch" not in name:
                devices.append({"id": device_id, "name": name, "model": "iOS Device", "os": os_ver})
    return devices


def parse_ios_deploy_devices(output: str, seen_ids: set[str]) -> list[dict]:
    devices = []
    for line in output.splitlines():
        match = re.search(r"Found ([A-Fa-f0-9-]+) \(([^,]+), ([^,]+)", line)
        if match:
            device_id, _, model = match.groups()
            if device_id not in seen_ids and "Watch" not in line:
                name_match = re.search(r"'([^']+)'", line)
                name = name_match.group(1) if name_match else model
                devices.append({"id": device_id, "name": name, "model": model.strip(), "os": ""})
    return devices


def parse_devicectl_devices(output: str) -> list[dict]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    devices = []
    for device in data.get("result", {}).get("devices", []):
        if device.get("connectionProperties", {}).get("transportType") == "wired":
            devices.append(
                {
                    "id": device.get("identifier"),
                    "name": device.get("deviceProperties", {}).get("name"),
                    "model": device.get("deviceProperties", {}).get("marketingName"),
                    "os": device.get("deviceProperties", {}).get("osVersionNumber"),
                }
            )
    return devices


# ##################################################################
# find connected devices
# finds all connected ios devices using xctrace and ios-deploy
def find_connected_devices() -> list[dict]:
    devices = []
    seen_ids = set()

    # Try xctrace first (most reliable for USB)
    ret_code, output = exec_cmd(["xcrun", "xctrace", "list", "devices"], timeout=30)

    if ret_code == 0:
        devices.extend(parse_xctrace_devices(output))
        seen_ids.update(device["id"] for device in devices)

    # Also try ios-deploy -c (finds WiFi devices xctrace misses)
    ret_code, output = exec_cmd(
        ["ios-deploy", "-c", "--timeout", "5"],
        timeout=15,
    )

    if ret_code == 0:
        discovered = parse_ios_deploy_devices(output, seen_ids)
        devices.extend(discovered)
        seen_ids.update(device["id"] for device in discovered)

    # Try devicectl if nothing was found yet
    if not devices:
        ret_code, output = exec_cmd(
            ["xcrun", "devicectl", "list", "devices", "--json-output", "/dev/stdout"],
            timeout=30,
        )

        if ret_code == 0:
            devices.extend(parse_devicectl_devices(output))

    return devices


def load_auth_args(asc_dir: Path) -> list[str]:
    api_key_json = asc_dir / "api_key.json"
    if not api_key_json.exists():
        return []
    try:
        data = json.loads(api_key_json.read_text())
        key_id = data.get("key_id")
        issuer_id = data.get("issuer_id")
        p8_path = asc_dir / "private_keys" / f"AuthKey_{key_id}.p8"
        if key_id and issuer_id and p8_path.exists():
            return [
                "-authenticationKeyID",
                key_id,
                "-authenticationKeyIssuerID",
                issuer_id,
                "-authenticationKeyPath",
                str(p8_path),
            ]
    except (json.JSONDecodeError, OSError):
        return []
    return []


# ##################################################################
# build for device
# builds the app for a real device using development signing
def build_for_device(project_path: Path, bundle_id: str) -> str | None:
    project_type, xcodeproj, scheme = detect_project_type(project_path)

    if project_type == "unknown":
        print_error("No Xcode project found (checked ios/App/ and project root)")
        return None

    print_info(f"Building {scheme} ({project_type} project) for device...")

    # regenerate xcodeproj from project.yml if present
    if (project_path / "project.yml").exists():
        print_info("Regenerating Xcode project from project.yml...")
        ret_code, output = exec_cmd(["xcodegen", "generate"], cwd=project_path, timeout=60)
        if ret_code != 0:
            print_error(f"xcodegen failed: {output}")
            return None

    build_dir = project_path / "build" / "device"
    build_dir.mkdir(parents=True, exist_ok=True)

    # Automatic signing only — NO fastlane match, ever.
    # -allowProvisioningUpdates lets Xcode create/refresh the dev profile
    # against the developer portal using the App Store Connect API key.
    asc_dir = Path.home() / ".appstoreconnect"
    auth_args = load_auth_args(asc_dir)

    xcb_args = [
        "xcrun",
        "xcodebuild",
        "-project",
        str(xcodeproj),
        "-scheme",
        scheme,
        "-configuration",
        "Debug",
        "-destination",
        "generic/platform=iOS",
        "-derivedDataPath",
        str(build_dir),
        "-allowProvisioningUpdates",
        *auth_args,
        f"DEVELOPMENT_TEAM={TEAM_ID}",
        "CODE_SIGN_STYLE=Automatic",
        "CODE_SIGN_IDENTITY=Apple Development",
        "PROVISIONING_PROFILE_SPECIFIER=",
        "build",
    ]
    # macOS Tahoe: the Metal Toolchain shim fails when the calling shell's
    # group set includes 'daemon' (cryptex mount perms quirk). Bounce through
    # `sudo -u $USER bash -c` so we get a clean login process group.
    import os
    import shlex

    user = os.environ.get("USER") or "darrenoakey"
    quoted = " ".join(shlex.quote(a) for a in xcb_args)
    cmd = [
        "sudo",
        "-n",
        "-u",
        user,
        "bash",
        "-c",
        f"cd {shlex.quote(str(project_path))} && {quoted}",
    ]

    ret_code, output = exec_cmd(cmd, cwd=project_path, timeout=900)

    if ret_code != 0:
        print_error("Build failed")
        # show last few lines of build output for debugging
        lines = output.strip().split("\n")
        for line in lines[-10:]:
            if "error:" in line.lower():
                print_error(f"  {line.strip()}")
        return None

    # Find the app bundle - try exact path first
    app_name = f"{scheme}.app"
    app_path = build_dir / "Build" / "Products" / "Debug-iphoneos" / app_name
    if app_path.exists():
        return str(app_path)

    # Search for any .app in the build output
    for app in build_dir.rglob("*.app"):
        if "Debug-iphoneos" in str(app):
            return str(app)

    print_error("Could not find built app bundle")
    return None


# ##################################################################
# install on device
# installs app on connected device - tries ios-deploy first (reliable),
# falls back to devicectl
def install_on_device(app_path: str, device_id: str | None = None) -> bool:
    print_info(f"Installing {Path(app_path).name}...")

    # Try ios-deploy first (works over USB and WiFi, most reliable)
    cmd = ["ios-deploy", "--bundle", app_path]
    if device_id:
        cmd.extend(["--id", device_id])

    ret_code, output = exec_cmd(cmd, timeout=120)

    if ret_code == 0:
        return True

    # Try devicectl next (Xcode 15+)
    print_info("ios-deploy failed, trying devicectl...")
    cmd = ["xcrun", "devicectl", "device", "install", "app"]
    if device_id:
        cmd.extend(["--device", device_id])
    cmd.append(app_path)

    ret_code, output = exec_cmd(cmd, timeout=120)

    if ret_code == 0:
        return True

    print_error("Installation failed")
    print_info("Make sure device is unlocked and trusts this computer")
    return False


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

    # Auto-detect bundle_id if not in state
    bundle_id = state.bundle_id
    if not bundle_id:
        bundle_id = detect_bundle_id(project_path)
        if not bundle_id:
            print_error("Could not detect bundle ID")
            print_info("Ensure capacitor.config.json or project.yml exists")
            return False
        print_info(f"Detected bundle ID: {bundle_id}")

    # Auto-detect project type for sync
    project_type = state.project_type
    if not project_type:
        project_type, _, _ = detect_project_type(project_path)

    # Ensure development provisioning profile
    if not ensure_dev_profile(bundle_id):
        return False

    # Sync web content if capacitor project
    if project_type == "web":
        if not sync_web_content(project_path):
            return False

    # Build for device
    app_path = build_for_device(project_path, bundle_id)
    if not app_path:
        return False

    print_success(f"Built: {app_path}")

    # Install
    if not install_on_device(app_path, device_id):
        return False

    print_success(f"Deployed to {device['name']}!")
    return True


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
