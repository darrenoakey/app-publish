from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState, load_state, save_state
from config import TEAM_ID
from modules.signing import sign_app_bundle
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    ensure_dir,
    file_exists,
    dir_exists,
    parse_schemes,
    pick_scheme,
)


# ##################################################################
# sync web content
# sync web content to ios project using capacitor
def sync_web_content(project_path: Path) -> bool:
    has_cap = (
        (project_path / "capacitor.config.ts").exists()
        or (project_path / "capacitor.config.json").exists()
        or (project_path / "capacitor.config.js").exists()
    )
    if not has_cap:
        return True
    ret_code, output = exec_cmd(
        ["npx", "--no-install", "cap", "sync", "ios"],
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
# xcode bundle ready
# xcodebuild hangs on empty .xcodeproj folders; require the real file
def xcode_bundle_ready(path: str) -> bool:
    bundle = Path(path)
    if bundle.suffix == ".xcworkspace":
        return (bundle / "contents.xcworkspacedata").is_file()
    return (bundle / "project.pbxproj").is_file()


# ##################################################################
# find scheme
# find the xcode scheme to build
def find_scheme(project_path: Path, state: ProjectState) -> str:
    xcode_project = find_xcode_project(project_path, state)

    if not xcode_project or not xcode_bundle_ready(xcode_project):
        return "App"  # default capacitor scheme

    # list schemes
    use_workspace = state.metadata.get("use_workspace", False)
    list_cmd = ["xcodebuild", "-list"]
    if use_workspace:
        list_cmd.extend(["-workspace", xcode_project])
    else:
        list_cmd.extend(["-project", xcode_project])

    ret_code, output = exec_cmd(list_cmd)

    if ret_code == 0:
        schemes = parse_schemes(output)
        if schemes:
            return pick_scheme(schemes, Path(xcode_project).stem)

    return "App"  # default


# ##################################################################
# find scheme
# find the xcode scheme to build


# ##################################################################
# build archive
# build and archive the app
def build_archive(project_path: Path, state: ProjectState) -> bool:
    xcode_project = find_xcode_project(project_path, state)
    if not xcode_project:
        print_error("No Xcode project found")
        return False
    if not xcode_bundle_ready(xcode_project):
        print_error(f"Xcode project is incomplete: {xcode_project}")
        return False

    scheme = find_scheme(project_path, state)
    print_info(f"Building scheme: {scheme}")
    print_info(f"Using project: {xcode_project}")

    use_workspace = state.metadata.get("use_workspace", False)

    # build directory - use local temp for external drives (rsync issues)
    import tempfile

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

    # Build unsigned. The finished bundle is provisioned and signed below by
    # rcodesign with the P12 supplied through an anonymous pipe.
    archive_cmd.extend(
        [
            "-scheme",
            scheme,
            "-configuration",
            "Release",
            "-archivePath",
            str(archive_path),
            "-destination",
            "generic/platform=iOS",
            "CURRENT_PROJECT_VERSION=" + str(state.current_build),
            f"MARKETING_VERSION={state.current_version}",
            f"DEVELOPMENT_TEAM={TEAM_ID}",
            f"PRODUCT_BUNDLE_IDENTIFIER={state.bundle_id}",
            "CODE_SIGNING_ALLOWED=NO",
            "CODE_SIGNING_REQUIRED=NO",
            "archive",
        ]
    )

    ret_code, output = exec_cmd(archive_cmd, timeout=600)

    if ret_code != 0:
        print_error(f"Archive failed: {output}")
        return False

    if not dir_exists(archive_path):
        print_error("Archive was not created")
        return False

    print_success(f"Archive created: {archive_path}")
    state.metadata["archive_path"] = str(archive_path)

    applications = archive_path / "Products" / "Applications"
    app_bundles = list(applications.glob("*.app"))
    if len(app_bundles) != 1:
        print_error(f"Expected one app bundle in archive, found {len(app_bundles)}")
        return False
    app_name = state.metadata.get("app_name") or state.project_name
    if not sign_app_bundle(
        app_bundles[0],
        state.bundle_id,
        app_name,
        "IOS_APP_STORE",
    ):
        return False

    # Package the already signed bundle directly. xcodebuild -exportArchive
    # would route signing back through macOS Keychain.
    export_path = build_dir / "export"
    ensure_dir(export_path)
    ipa_path = create_ipa_manually(archive_path, export_path, state)
    if not ipa_path:
        print_error("IPA packaging failed")
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
    ret_code, output = exec_cmd(["ditto", str(app_bundle), str(dest_app)])

    if ret_code != 0:
        print_error(f"Failed to copy app bundle: {output}")
        return None

    # create the ipa (zip file)
    ipa_path = export_path / f"{state.project_name}.ipa"

    # use ditto to create the zip (better than zipfile for macos)
    ret_code, output = exec_cmd(["ditto", "-c", "-k", "--keepParent", str(payload_dir), str(ipa_path)])

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
