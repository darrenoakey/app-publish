from pathlib import Path

from modules.build import (
    build_archive,
    create_ipa_manually,
    find_scheme,
    find_xcode_project,
    run,
    sync_web_content,
    xcode_bundle_ready,
)
from state import ProjectState


def test_sync_web_content_runs_without_raising(tmp_path: Path) -> None:
    assert sync_web_content(tmp_path) is True
    (tmp_path / "capacitor.config.json").write_text("{}")
    assert sync_web_content(tmp_path) is True


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
