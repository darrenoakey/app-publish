from pathlib import Path
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState, load_state, save_state
from config import TEAM_ID
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


# ##################################################################
# sync web content
# sync web content to ios project using capacitor
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
# ##################################################################
# sync web content
# sync web content to ios project using capacitor


# ##################################################################
# find xcode project
# find the xcode project or workspace path
def find_xcode_project(project_path: Path, state: ProjectState) -> str:
    xcode_project = state.metadata.get("xcode_project", "")

    if xcode_project and file_exists(Path(xcode_project)):
        return xcode_project

    # for capacitor projects, look in ios/App/
    ios_app_dir = project_path / "ios" / "App"
    if dir_exists(ios_app_dir):
        # prefer workspace for capacitor
        xcworkspace = list(ios_app_dir.glob("*.xcworkspace"))
        if xcworkspace:
            xcode_project = str(xcworkspace[0])
            state.metadata["xcode_project"] = xcode_project
            state.metadata["use_workspace"] = True
            return xcode_project

        xcodeproj = list(ios_app_dir.glob("*.xcodeproj"))
        if xcodeproj:
            xcode_project = str(xcodeproj[0])
            state.metadata["xcode_project"] = xcode_project
            return xcode_project

    # for native projects, look in ios/ or root
    ios_dir = project_path / "ios"
    if dir_exists(ios_dir):
        xcodeproj = list(ios_dir.glob("*.xcodeproj"))
        if xcodeproj:
            xcode_project = str(xcodeproj[0])
            state.metadata["xcode_project"] = xcode_project
            return xcode_project

    # root level
    xcodeproj = list(project_path.glob("*.xcodeproj"))
    if xcodeproj:
        xcode_project = str(xcodeproj[0])
        state.metadata["xcode_project"] = xcode_project
        return xcode_project

    return ""
# ##################################################################
# find xcode project
# find the xcode project or workspace path


# ##################################################################
# find scheme
# find the xcode scheme to build
def find_scheme(project_path: Path, state: ProjectState) -> str:
    xcode_project = find_xcode_project(project_path, state)

    if not xcode_project:
        return "App"  # default capacitor scheme

    # list schemes
    use_workspace = state.metadata.get("use_workspace", False)
    list_cmd = ["xcodebuild", "-list"]
    if use_workspace:
        list_cmd.extend(["-workspace", xcode_project])
    else:
        list_cmd.extend(["-project", xcode_project])

    ret_code, output = exec_cmd(list_cmd)

    if ret_code == 0 and "Schemes:" in output:
        # parse schemes
        lines = output.split("\n")
        in_schemes = False
        for line in lines:
            if "Schemes:" in line:
                in_schemes = True
                continue
            if in_schemes and line.strip():
                return line.strip()

    return "App"  # default
# ##################################################################
# find scheme
# find the xcode scheme to build


# ##################################################################
# create export options
# create exportoptions.plist for app store export
def create_export_options(project_path: Path, state: ProjectState) -> Path:
    export_options = project_path / "ExportOptions.plist"

    # use 'app-store' method (not 'app-store-connect') to just create the ipa
    # without requiring app store connect authentication at build time
    content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>method</key>
    <string>app-store</string>
    <key>teamID</key>
    <string>{TEAM_ID}</string>
    <key>uploadSymbols</key>
    <true/>
    <key>signingStyle</key>
    <string>manual</string>
    <key>signingCertificate</key>
    <string>Apple Distribution</string>
    <key>provisioningProfiles</key>
    <dict>
        <key>{state.bundle_id}</key>
        <string>match AppStore {state.bundle_id}</string>
    </dict>
</dict>
</plist>
'''
    write_file(export_options, content)
    return export_options
# ##################################################################
# create export options
# create exportoptions.plist for app store export


# ##################################################################
# build archive
# build and archive the app
def build_archive(project_path: Path, state: ProjectState) -> bool:
    xcode_project = find_xcode_project(project_path, state)
    if not xcode_project:
        print_error("No Xcode project found")
        return False

    scheme = find_scheme(project_path, state)
    print_info(f"Building scheme: {scheme}")
    print_info(f"Using project: {xcode_project}")

    use_workspace = state.metadata.get("use_workspace", False)

    # build directory - use local temp for external drives (rsync issues)
    import tempfile
    import shutil

    if str(project_path).startswith("/Volumes/"):
        # use local temp directory for builds on external drives
        temp_build_dir = Path(tempfile.mkdtemp(prefix="app-publish-build-"))
        build_dir = temp_build_dir
        print_info(f"Using local build path: {build_dir}")
    else:
        build_dir = project_path / "build"

    ensure_dir(build_dir)

    archive_path = build_dir / f"{state.project_name}.xcarchive"

    # increment build number
    state.current_build += 1
    print_info(f"Build number: {state.current_build}")

    # archive
    print_info("Creating archive...")
    archive_cmd = ["xcodebuild"]
    if use_workspace:
        archive_cmd.extend(["-workspace", xcode_project])
    else:
        archive_cmd.extend(["-project", xcode_project])

    archive_cmd.extend([
        "-scheme", scheme,
        "-configuration", "Release",
        "-archivePath", str(archive_path),
        "-destination", "generic/platform=iOS",
        "CURRENT_PROJECT_VERSION=" + str(state.current_build),
        f"DEVELOPMENT_TEAM={TEAM_ID}",
        f"PRODUCT_BUNDLE_IDENTIFIER={state.bundle_id}",
        "CODE_SIGN_STYLE=Manual",
        "CODE_SIGN_IDENTITY=Apple Distribution",
        f"PROVISIONING_PROFILE_SPECIFIER=match AppStore {state.bundle_id}",
        "archive",
    ])

    ret_code, output = exec_cmd(archive_cmd, timeout=600)

    if ret_code != 0:
        print_error(f"Archive failed: {output}")
        return False

    if not dir_exists(archive_path):
        print_error("Archive was not created")
        return False

    print_success(f"Archive created: {archive_path}")
    state.metadata["archive_path"] = str(archive_path)

    # export ipa - try multiple methods
    export_path = build_dir / "export"
    ensure_dir(export_path)

    ipa_path = None
    export_succeeded = False

    # method 1: try fastlane gym
    print_info("Exporting IPA for App Store using fastlane...")
    gym_cmd = [
        "fastlane", "gym",
        "--skip_build_archive", "true",
        "--archive_path", str(archive_path),
        "--export_method", "app-store",
        "--output_directory", str(export_path),
        "--output_name", state.project_name,
        "--export_team_id", TEAM_ID,
    ]

    ret_code, output = exec_cmd(gym_cmd, cwd=project_path, timeout=300)

    if ret_code == 0:
        ipa_files = list(export_path.glob("*.ipa"))
        if ipa_files:
            ipa_path = ipa_files[0]
            export_succeeded = True

    # method 2: try xcodebuild exportarchive
    if not export_succeeded:
        print_warning("Fastlane export failed, trying xcodebuild...")
        export_options = create_export_options(project_path, state)

        ret_code, output = exec_cmd([
            "xcodebuild",
            "-exportArchive",
            "-archivePath", str(archive_path),
            "-exportPath", str(export_path),
            "-exportOptionsPlist", str(export_options),
        ], timeout=300)

        if ret_code == 0:
            ipa_files = list(export_path.glob("*.ipa"))
            if ipa_files:
                ipa_path = ipa_files[0]
                export_succeeded = True
        else:
            print_warning(f"xcodebuild export also failed: {output[:200]}...")

    # method 3: manual ipa creation (bypasses rsync issues)
    if not export_succeeded:
        print_warning("Standard exports failed, creating IPA manually...")
        ipa_path = create_ipa_manually(archive_path, export_path, state)
        if ipa_path:
            export_succeeded = True

    if not export_succeeded or not ipa_path:
        print_error("All IPA export methods failed")
        return False

    print_success(f"IPA created: {ipa_path}")
    state.metadata["ipa_path"] = str(ipa_path)

    return True
# ##################################################################
# build archive
# build and archive the app


# ##################################################################
# create ipa manually
# create ipa manually from xcarchive without using xcodebuild exportarchive
# this bypasses the rsync issue on macos where openrsync doesn't support -E flag
# an ipa is just a zip file containing payload/app.app
def create_ipa_manually(archive_path: Path, export_path: Path, state: ProjectState) -> Path | None:
    import shutil
    import zipfile

    # find the .app inside the archive
    apps_dir = archive_path / "Products" / "Applications"
    if not dir_exists(apps_dir):
        print_error(f"No Applications directory in archive: {apps_dir}")
        return None

    app_bundles = list(apps_dir.glob("*.app"))
    if not app_bundles:
        print_error("No .app bundle found in archive")
        return None

    app_bundle = app_bundles[0]
    print_info(f"Found app bundle: {app_bundle.name}")

    # create payload directory structure
    payload_dir = export_path / "Payload"
    if dir_exists(payload_dir):
        shutil.rmtree(payload_dir)
    ensure_dir(payload_dir)

    # copy .app to payload/ using ditto (preserves extended attributes properly)
    dest_app = payload_dir / app_bundle.name
    ret_code, output = exec_cmd([
        "ditto", str(app_bundle), str(dest_app)
    ])

    if ret_code != 0:
        print_error(f"Failed to copy app bundle: {output}")
        return None

    # create the ipa (zip file)
    ipa_path = export_path / f"{state.project_name}.ipa"

    # use ditto to create the zip (better than zipfile for macos)
    ret_code, output = exec_cmd([
        "ditto", "-c", "-k", "--keepParent",
        str(payload_dir), str(ipa_path)
    ])

    if ret_code != 0:
        print_error(f"Failed to create IPA: {output}")
        return None

    # clean up payload directory
    shutil.rmtree(payload_dir)

    if file_exists(ipa_path):
        return ipa_path

    return None
# ##################################################################
# create ipa manually
# create ipa manually from xcarchive without using xcodebuild exportarchive


# ##################################################################
# run
# run build step
# creates build/*.xcarchive and build/export/*.ipa
def run(project_path: Path, state: ProjectState) -> bool:
    # sync web content if web project
    if state.project_type == "web":
        if not sync_web_content(project_path):
            return False

    # build archive and export ipa
    if not build_archive(project_path, state):
        return False

    return True
# ##################################################################
# run
# run build step
# creates build/*.xcarchive and build/export/*.ipa


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python build.py <project_path>")
        sys.exit(1)

    project_path = Path(sys.argv[1]).resolve()
    state = load_state(project_path)

    if not state.bundle_id:
        print_error("No bundle_id in state - run structure step first")
        sys.exit(1)

    success = run(project_path, state)
    if success:
        save_state(project_path, state)
        print_success("Build step completed successfully!")
    else:
        print_error("Build step failed!")
        sys.exit(1)
