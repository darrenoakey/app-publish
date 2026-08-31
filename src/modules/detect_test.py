from modules.detect import (
    detect_bundle_id,
    detect_existing_ios_project,
    detect_project_type,
    generate_bundle_id,
    run,
)
from state import ProjectState


def test_detection_prefers_xcode_and_parses_bundle_identifier(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<main>web</main>")
    project = tmp_path / "Reader.xcodeproj"
    project.mkdir()
    (project / "project.pbxproj").write_text('PRODUCT_BUNDLE_IDENTIFIER = "com.example.reader";\n')
    kind, reason = detect_project_type(tmp_path)
    assert kind == "swift"
    assert "*.xcodeproj" in reason
    assert detect_existing_ios_project(tmp_path) == project
    assert detect_bundle_id(tmp_path) == "com.example.reader"
    generated = generate_bundle_id("42 quiet-reader_app")
    assert generated.endswith("app42QuietReaderApp")


def test_detection_covers_web_swift_and_unidentified_real_directories(tmp_path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "package.json").write_text("{}")
    assert detect_project_type(web) == ("web", "Found package.json")

    nested = tmp_path / "nested"
    (nested / "src").mkdir(parents=True)
    (nested / "src" / "index.html").write_text("<main>nested</main>")
    assert detect_project_type(nested) == ("web", "Found src/index.html")

    html = tmp_path / "html"
    html.mkdir()
    (html / "reader.html").write_text("<main>reader</main>")
    assert detect_project_type(html) == ("web", "Found HTML files: reader.html")

    swift = tmp_path / "swift"
    (swift / "Sources").mkdir(parents=True)
    (swift / "Sources" / "Reader.swift").write_text("struct Reader {}")
    kind, reason = detect_project_type(swift)
    assert kind == "swift"
    assert reason == "Found Swift files: Reader.swift"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert detect_project_type(empty) == (
        "web",
        "No specific indicators found, defaulting to web",
    )


def test_ios_project_bundle_and_run_state_are_derived_from_real_files(tmp_path) -> None:
    ios = tmp_path / "ios"
    project = ios / "Reader.xcodeproj"
    project.mkdir(parents=True)
    pbxproj = project / "project.pbxproj"
    pbxproj.write_text("// no bundle setting here\n")
    assert detect_existing_ios_project(tmp_path) == project
    assert detect_bundle_id(tmp_path) is None
    pbxproj.write_text("PRODUCT_BUNDLE_IDENTIFIER = com.example.reader;\n")
    assert detect_bundle_id(tmp_path) == "com.example.reader"
    assert detect_existing_ios_project(tmp_path / "absent") is None
    assert detect_bundle_id(tmp_path / "absent") is None

    state = ProjectState()
    assert run(tmp_path, state) is True
    assert state.project_type == "web"
    assert state.bundle_id == "com.example.reader"
    assert state.metadata["has_existing_ios"] is True
    assert state.metadata["xcode_project"] == str(project)
    assert state.metadata["file_count"] == 1

    web = tmp_path / "web-run"
    (web / "dist").mkdir(parents=True)
    (web / "dist" / "index.html").write_text("<main>built</main>")
    web_state = ProjectState(bundle_id="com.example.kept")
    assert run(web, web_state) is True
    assert web_state.bundle_id == "com.example.kept"
    assert web_state.metadata["has_existing_ios"] is False
    assert web_state.metadata["entry_point"] == "dist/index.html"
    assert web_state.metadata["needs_build"] is True


def test_capacitor_layout_detects_nested_xcode_and_config_identity(tmp_path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<main>golf</main>")
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "capacitor.config.json").write_text(
        '{"appId":"com.darrenoakey.olGolf","appName":"OL Golf","webDir":"web"}'
    )
    xcode = tmp_path / "ios" / "App" / "App.xcodeproj"
    xcode.mkdir(parents=True)
    (xcode / "project.pbxproj").write_text("PRODUCT_BUNDLE_IDENTIFIER = com.darrenoakey.olGolf;\n")

    assert detect_existing_ios_project(tmp_path) == xcode
    assert detect_bundle_id(tmp_path) == "com.darrenoakey.olGolf"

    state = ProjectState()
    assert run(tmp_path, state) is True
    assert state.project_type == "web"
    assert state.bundle_id == "com.darrenoakey.olGolf"
    assert state.app_name == "OL Golf"
    assert state.metadata["has_existing_ios"] is True
    assert state.metadata["xcode_project"] == str(xcode)
    assert state.metadata["entry_point"] == "web/index.html"


def test_bundle_identifier_generation_handles_letters_numbers_and_symbols() -> None:
    assert generate_bundle_id("Reader").endswith("reader")
    assert generate_bundle_id("123").endswith("app123")
    assert generate_bundle_id("---").endswith("")
