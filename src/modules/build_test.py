from pathlib import Path

from modules.build import (
    archive_command,
    build_archive,
    create_ipa_manually,
    find_scheme,
    find_xcode_project,
    run,
    sync_web_content,
    xcode_bundle_ready,
)
from state import ProjectState


def test_sync_web_content_fails_when_capacitor_sync_fails(tmp_path: Path) -> None:
    # no capacitor config: nothing to sync
    assert sync_web_content(tmp_path) is True
    # capacitor project whose cli is missing (no node_modules): the real npx call
    # exits nonzero and the build must refuse to continue with stale web content
    (tmp_path / "capacitor.config.json").write_text("{}")
    assert sync_web_content(tmp_path) is False
    # a working cli lets the build proceed
    command_directory = tmp_path / "node_modules" / ".bin"
    command_directory.mkdir(parents=True)
    (command_directory / "cap").symlink_to("/usr/bin/true")
    assert sync_web_content(tmp_path) is True


def test_archive_command_uses_persistent_project_local_derived_data(tmp_path: Path) -> None:
    state = ProjectState(
        project_name="Reader",
        current_build=7,
        current_version="2.3.4",
        bundle_id="com.darrenoakey.reader",
    )
    archive_path = tmp_path / "build" / "Reader.xcarchive"
    derived_data_path = tmp_path / "local" / "appstore-derived-data"
    command = archive_command(
        tmp_path,
        str(tmp_path / "Reader.xcodeproj"),
        "Reader",
        archive_path,
        state,
        use_workspace=False,
    )

    assert derived_data_path.is_dir()
    assert command[command.index("-derivedDataPath") + 1] == str(derived_data_path)
    assert command[command.index("-archivePath") + 1] == str(archive_path)
    assert command[command.index("-project") + 1] == str(tmp_path / "Reader.xcodeproj")
    assert "CURRENT_PROJECT_VERSION=7" in command
    assert "MARKETING_VERSION=2.3.4" in command
    assert "PRODUCT_BUNDLE_IDENTIFIER=com.darrenoakey.reader" in command
    assert "CODE_SIGNING_ALLOWED=NO" in command
    assert "CODE_SIGNING_REQUIRED=NO" in command
    assert command[-1] == "archive"


def test_xcode_discovery_covers_all_project_layouts(tmp_path: Path) -> None:
    # 1. missing project
    state = ProjectState()
    assert find_xcode_project(tmp_path, state) == ""
    assert find_scheme(tmp_path, state) == "App"
    assert not build_archive(tmp_path, state)
    assert not run(tmp_path, state)

    # 2. capacitor workspace
    ios_app = tmp_path / "ios" / "App"
    ios_app.mkdir(parents=True)
    workspace = ios_app / "Product.xcworkspace"
    workspace.mkdir()
    state_ws = ProjectState()
    assert find_xcode_project(tmp_path, state_ws) == str(workspace)
    assert state_ws.metadata["use_workspace"] is True
    workspace.rmdir()

    # 3. capacitor xcodeproj
    cap_project = ios_app / "Product.xcodeproj"
    cap_project.mkdir()
    state_cap = ProjectState()
    assert find_xcode_project(tmp_path, state_cap) == str(cap_project)
    cap_project.rmdir()

    # 4. native ios xcodeproj
    native_proj = tmp_path / "ios" / "Native.xcodeproj"
    native_proj.mkdir()
    state_native = ProjectState()
    assert find_xcode_project(tmp_path, state_native) == str(native_proj)
    native_proj.rmdir()

    # 5. root xcodeproj
    root_proj = tmp_path / "Root.xcodeproj"
    root_proj.mkdir()
    state_root = ProjectState()
    assert find_xcode_project(tmp_path, state_root) == str(root_proj)


def test_xcode_bundle_ready(tmp_path: Path) -> None:
    ws = tmp_path / "Test.xcworkspace"
    ws.mkdir()
    assert xcode_bundle_ready(str(ws)) is False
    (ws / "contents.xcworkspacedata").write_text("<Workspace></Workspace>")
    assert xcode_bundle_ready(str(ws)) is True

    proj = tmp_path / "Test.xcodeproj"
    proj.mkdir()
    assert xcode_bundle_ready(str(proj)) is False
    (proj / "project.pbxproj").write_text("archiveVersion = 1;\n")
    assert xcode_bundle_ready(str(proj)) is True


def test_run_orchestrates_web_and_native_build_steps(tmp_path: Path) -> None:
    web_state = ProjectState(project_type="web", project_name="Reader")
    assert run(tmp_path, web_state) is False

    native_state = ProjectState(project_type="swift", project_name="Reader")
    assert run(tmp_path, native_state) is False


def test_manual_ipa_creation_uses_real_local_archive_tools(tmp_path: Path) -> None:
    state = ProjectState(project_name="Reader")
    archive = tmp_path / "Reader.xcarchive"
    export = tmp_path / "export"
    assert create_ipa_manually(archive, export, state) is None

    applications = archive / "Products" / "Applications"
    applications.mkdir(parents=True)
    assert create_ipa_manually(archive, export, state) is None

    app = applications / "Reader.app"
    app.mkdir()
    (app / "Info.plist").write_text("bundle contents")
    ipa = create_ipa_manually(archive, export, state)
    assert ipa == export / "Reader.ipa"
    assert ipa.is_file()
    assert not (export / "Payload").exists()
