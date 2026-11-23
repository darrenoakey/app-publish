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
import apple_portal
from modules.signing import requested_entitlements, sign_app_bundle
from state import ProjectState, load_state
from modules.device_guard import RELEASE, assert_release_only
from utils import (
    capacitor_sync,
    print_info,
    print_success,
    print_error,
    run as exec_cmd,
    parse_schemes,
    pick_scheme,
)


# ##################################################################
# detect bundle id
# auto-detect bundle id from capacitor.config.json or project.yml
def _bundle_id_from_project_yml(project_yml: Path) -> str | None:
    """Read PRODUCT_BUNDLE_IDENTIFIER from an XcodeGen spec, app target only.

    The first match in the file is normally the application target, but a spec
    can also declare test targets first. Prefer the identifier that is not a
    test/UITest bundle so we do not sign the test runner.
    """
    if not project_yml.is_file():
        return None
    text = project_yml.read_text()
    matches = re.findall(r'PRODUCT_BUNDLE_IDENTIFIER:\s*["\']?([^\s"\']+)', text)
    if not matches:
        return None
    for candidate in matches:
        lowered = candidate.lower()
        if not lowered.endswith((".tests", ".uitests", ".unittests")):
            return candidate
    return matches[0]


def detect_bundle_id(project_path: Path) -> str | None:
    # Many repos keep the Xcode project under ios/ rather than the repo root
    # (agentd3-ios does), so probe the root first and then the usual iOS dirs.
    search_dirs = [project_path, project_path / "ios", project_path / "ios" / "App"]

    for directory in search_dirs:
        cap_config = directory / "capacitor.config.json"
        if cap_config.is_file():
            try:
                bid = json.loads(cap_config.read_text()).get("appId")
                if bid:
                    return bid
            except json.JSONDecodeError:
                pass

    for directory in search_dirs:
        bid = _bundle_id_from_project_yml(directory / "project.yml")
        if bid:
            return bid

    # try finding xcodeproj and reading build settings
    for directory in search_dirs:
        for xcodeproj in directory.glob("*.xcodeproj"):
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

    # native project - an Xcode project can sit at the repo root or under ios/
    # (agentd3-ios keeps ios/agentd3-ios.xcodeproj), so search both.
    for directory in (project_path, project_path / "ios"):
        for xcodeproj in sorted(directory.glob("*.xcodeproj")):
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
    return capacitor_sync(project_path, no_install=False)


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


# Physical transports CoreDevice reports for a real, reachable iPhone. A
# device paired over Wi-Fi reports "localNetwork" instead of "wired", and
# dropping it made Apollo look absent while it was in fact installable over
# the tunnel. Simulators report "sameMachine" and stay excluded.
_PHYSICAL_TRANSPORTS = {"wired", "localNetwork"}


def parse_devicectl_devices(output: str) -> list[dict]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    devices = []
    for device in data.get("result", {}).get("devices", []):
        if device.get("connectionProperties", {}).get("transportType") in _PHYSICAL_TRANSPORTS:
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

    # Probe USB (xctrace) and USB/WiFi (ios-deploy) concurrently. Each tool
    # can be the only one that sees the device, and serial probing costs
    # ~18s before every deploy.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        xctrace_future = pool.submit(exec_cmd, ["xcrun", "xctrace", "list", "devices"], timeout=30)
        ios_deploy_future = pool.submit(exec_cmd, ["ios-deploy", "-c", "--timeout", "5"], timeout=15)

        ret_code, output = xctrace_future.result()
        if ret_code == 0:
            devices.extend(parse_xctrace_devices(output))
            seen_ids.update(device["id"] for device in devices)

        ret_code, output = ios_deploy_future.result()
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


# ##################################################################
# build for device
# builds unsigned, then applies an ad-hoc distribution profile for the device
def build_for_device(
    project_path: Path,
    bundle_id: str,
    device_udid: str,
) -> str | None:
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
        f"DEVELOPMENT_TEAM={TEAM_ID}",
        "CODE_SIGNING_ALLOWED=NO",
        "CODE_SIGNING_REQUIRED=NO",
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
    try:
        requested = requested_entitlements(
            str(xcodeproj),
            scheme,
            "Debug",
            use_workspace=False,
        )
    except apple_portal.ApplePortalError as error:
        print_error(f"Source entitlement resolution failed: {error}")
        return None

    app_name = f"{scheme}.app"
    app_path = build_dir / "Build" / "Products" / "Debug-iphoneos" / app_name
    if app_path.exists():
        return (
            str(app_path)
            if sign_app_bundle(
                app_path,
                bundle_id,
                scheme,
                "IOS_APP_ADHOC",
                device_udids=[device_udid],
                requested=requested,
            )
            else None
        )

    # Search for any .app in the build output
    for app in build_dir.rglob("*.app"):
        if "Debug-iphoneos" in str(app):
            return (
                str(app)
                if sign_app_bundle(
                    app,
                    bundle_id,
                    scheme,
                    "IOS_APP_ADHOC",
                    device_udids=[device_udid],
                    requested=requested,
                )
                else None
            )

    print_error("Could not find built app bundle")
    return None


# ##################################################################
# install on device
# builds the installer command attempts in priority order:
# devicectl first (Xcode 15+, maintained, measured ~3s on iOS 27),
# ios-deploy last (unmaintained; measured hanging >180s on modern iOS).
def install_command_attempts(app_path: str, device_id: str | None = None) -> list[list[str]]:
    devicectl = ["xcrun", "devicectl", "device", "install", "app"]
    if device_id:
        devicectl.extend(["--device", device_id])
    devicectl.append(app_path)

    ios_deploy = ["ios-deploy", "--bundle", app_path]
    if device_id:
        ios_deploy.extend(["--id", device_id])

    return [devicectl, ios_deploy]


# installs app on connected device - tries each installer attempt in order
# (devicectl first, legacy ios-deploy last) until one succeeds
#
# A Wi-Fi-paired phone only ever answers devicectl: ios-deploy needs USB. A
# single devicectl attempt that loses a race with another CoreDevice client
# ("device is busy") therefore used to drop straight to an installer that could
# never work, and the deploy reported failure for an app that installs fine.
# Retry devicectl a few times before moving on to the legacy installer.
_DEVICECTL_ATTEMPTS = 3


def install_attempt_sequence(app_path: str, device_id: str | None = None) -> list[list[str]]:
    """Return installer commands in order; each devicectl invocation opens a fresh connection."""
    devicectl, ios_deploy = install_command_attempts(app_path, device_id)
    return [devicectl.copy() for _ in range(_DEVICECTL_ATTEMPTS)] + [ios_deploy]


def install_on_device(app_path: str, device_id: str | None = None) -> bool:
    print_info(f"Installing {Path(app_path).name}...")

    sequence = install_attempt_sequence(app_path, device_id)
    for index, cmd in enumerate(sequence):
        ret_code, _ = exec_cmd(cmd, timeout=120)
        if ret_code == 0:
            return True
        if index + 1 < len(sequence) and sequence[index + 1] != cmd:
            print_info(f"{' '.join(cmd[:2])} failed, trying next installer...")

    print_error("Installation failed")
    print_info("Make sure device is unlocked and trusts this computer")
    return False


# ##################################################################
# run
# deploys app to connected ios device
# device_name: name of device to deploy to (default: "Starbuck")
def run(project_path: Path, state: ProjectState, device_name: str = "Starbuck") -> bool:
    # Starbuck is the owner's working iPhone: install/launch of a release build only.
    assert_release_only([device_name], RELEASE)
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

    # Explicit installs must never switch to another phone when the target drops offline.
    matches = [d for d in devices if device_name.casefold() in (d["name"].casefold(), d["id"].casefold())]
    if len(matches) != 1 or not matches[0]["id"]:
        print_error(f"Device {device_name!r} is absent or ambiguous; refusing to install")
        return False
    device = matches[0]
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
    app_path = build_for_device(project_path, bundle_id, device_id)
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
