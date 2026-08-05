from modules.build import (
    build_archive,
    create_ipa_manually,
    find_scheme,
    find_xcode_project,
    run,
)
from state import ProjectState


def test_xcode_discovery(tmp_path) -> None:
    workspace = tmp_path / "ios" / "App" / "Product.xcworkspace"
    workspace.mkdir(parents=True)
    state = ProjectState()
    assert find_xcode_project(tmp_path, state) == str(workspace)
    assert state.metadata["use_workspace"] is True


def test_project_discovery_covers_native_locations_and_missing_projects(
    tmp_path,
) -> None:
    state = ProjectState(project_name="Reader")
    assert find_xcode_project(tmp_path, state) == ""
    assert find_scheme(tmp_path, state) == "App"
    assert not build_archive(tmp_path, state)
    assert not run(tmp_path, state)

    ios_project = tmp_path / "ios" / "Reader.xcodeproj"
    ios_project.mkdir(parents=True)
    assert find_xcode_project(tmp_path, ProjectState()) == str(ios_project)
    ios_project.rmdir()
    root_project = tmp_path / "Reader.xcodeproj"
    root_project.mkdir()
    cached = ProjectState(metadata={"xcode_project": str(root_project)})
    assert find_xcode_project(tmp_path, cached) == str(root_project)
    assert find_scheme(tmp_path, cached) == "App"
    cached.project_name = "Reader"
    cached.bundle_id = "com.example.reader"
    assert not build_archive(tmp_path, cached)
    assert cached.current_build == 1


def test_manual_ipa_creation_uses_real_local_archive_tools(tmp_path) -> None:
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
