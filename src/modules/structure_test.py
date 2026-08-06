import importlib

import modules.structure as structure_module
from modules.structure import (
    create_run_script,
    run,
    setup_fastlane,
    setup_swift_project,
    setup_web_project,
)
from state import ProjectState

importlib.reload(structure_module)


def test_structure_builds_fastlane_files_and_executable_runner(tmp_path) -> None:
    project = tmp_path / "Reader.xcodeproj"
    project.mkdir()
    state = ProjectState(project_name="Reader", app_name="Reader", bundle_id="com.example.reader")
    assert setup_swift_project(tmp_path, state)
    assert setup_fastlane(tmp_path, state)
    assert create_run_script(tmp_path, state)
    assert (tmp_path / "fastlane" / "Fastfile").is_file()
    assert (tmp_path / "fastlane" / "Snapfile").is_file()
    runner = tmp_path / "run"
    assert runner.stat().st_mode & 0o111
    assert "app-publish ." in runner.read_text()


def test_structure_rejects_incomplete_projects_using_real_directories(tmp_path, capsys) -> None:
    web = tmp_path / "web"
    (web / "node_modules" / "@capacitor").mkdir(parents=True)
    web_state = ProjectState(project_name="Reader", app_name="Reader", bundle_id="com.example.reader")
    assert setup_web_project(web, web_state) is False
    assert (web / "package.json").is_file()
    assert "No index.html found" in capsys.readouterr().out

    swift = tmp_path / "swift"
    swift.mkdir()
    swift_state = ProjectState(project_type="swift", project_name="Reader")
    assert setup_swift_project(swift, swift_state) is False
    assert run(swift, ProjectState(project_type="unknown")) is False


def test_structure_keeps_existing_capacitor_web_layout(tmp_path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<main>felt</main>")
    (tmp_path / "package.json").write_text('{"name":"ol-golf"}')
    (tmp_path / "node_modules" / "@capacitor").mkdir(parents=True)
    (tmp_path / "capacitor.config.json").write_text(
        '{"appId":"com.darrenoakey.olGolf","appName":"OL Golf","webDir":"web",'
        '"plugins":{"StatusBar":{"overlaysWebView":false}}}'
    )
    xcode = tmp_path / "ios" / "App" / "App.xcodeproj"
    xcode.mkdir(parents=True)
    (xcode / "project.pbxproj").write_text(
        "PRODUCT_BUNDLE_IDENTIFIER = com.darrenoakey.olGolf;\n"
    )

    state = ProjectState(
        project_type="web",
        project_name="ol-golf",
        bundle_id="com.darrenoakey.olGolf",
    )
    assert setup_web_project(tmp_path, state) is True
    assert state.app_name == "OL Golf"
    assert state.metadata["xcode_project"] == str(xcode)
    assert (tmp_path / "web" / "index.html").is_file()
    config = (tmp_path / "capacitor.config.json").read_text()
    assert '"webDir": "web"' in config
    assert '"appId": "com.darrenoakey.olGolf"' in config
    assert "StatusBar" in config



def test_structure_preserves_existing_local_automation_files(tmp_path) -> None:
    fastlane = tmp_path / "fastlane"
    fastlane.mkdir()
    runner = tmp_path / "run"
    runner.write_text("#!/bin/sh\nexit 0\n")
    state = ProjectState(project_name="Reader", bundle_id="com.example.reader")
    assert setup_fastlane(tmp_path, state) is True
    assert create_run_script(tmp_path, state) is True
    assert runner.read_text() == "#!/bin/sh\nexit 0\n"
