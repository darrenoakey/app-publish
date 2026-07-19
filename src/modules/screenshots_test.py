from PIL import Image

from modules.screenshots import (
    _get_generic_widget_content,
    analyze_screenshot_scenarios,
    are_images_similar,
    compute_image_hash,
    create_manual_capture_instructions,
    create_widget_screenshot_harness,
    crop_widget_screenshot,
    detect_widget_extension,
    find_ui_test_target,
    generate_screenshot_scenarios,
    generate_widget_sample_data,
    hamming_distance,
    remove_duplicate_screenshots,
    run_fastlane_snapshot,
)
from state import ProjectState


def test_perceptual_hash_and_device_scoped_duplicate_removal(tmp_path) -> None:
    first = tmp_path / "iPhone-01.png"
    duplicate = tmp_path / "iPhone-02.png"
    tablet = tmp_path / "iPad-01.png"
    Image.new("RGB", (32, 32), "navy").save(first)
    Image.new("RGB", (32, 32), "navy").save(duplicate)
    Image.new("RGB", (32, 32), "white").save(tablet)
    assert compute_image_hash(first) == compute_image_hash(duplicate)
    assert hamming_distance("0f", "0e") == 1
    assert hamming_distance("0", "00") == 64
    assert hamming_distance("zz", "yy") == 64
    assert are_images_similar(first, duplicate)
    assert remove_duplicate_screenshots(tmp_path) == 1
    assert first.exists() and not duplicate.exists() and tablet.exists()
    assert remove_duplicate_screenshots(tmp_path / "absent") == 0


def test_widget_detection_harness_and_image_presentation(tmp_path) -> None:
    widget_dir = tmp_path / "ReaderWidget"
    widget_dir.mkdir()
    (widget_dir / "ReaderWidget.swift").write_text(
        "import WidgetKit\nstruct ReaderEntryView: View {}\nlet family = WidgetFamily.systemMedium\n"
    )
    app_dir = tmp_path / tmp_path.name
    app_dir.mkdir()
    (app_dir / "Preview.swift").write_text("struct ReaderWidgetPreviewPanel: View {}\n")
    info = detect_widget_extension(tmp_path)
    assert info["has_widget"] is True
    assert info["widget_view"] == "ReaderEntryView"
    assert info["preview_view"] == "ReaderWidgetPreviewPanel"
    assert info["widget_family"] == "systemMedium"

    state = ProjectState(app_name="Reader", app_description="Reading progress")
    generic = generate_widget_sample_data(tmp_path, state, {"extension_dir": None})
    assert generic == _get_generic_widget_content()
    harness = create_widget_screenshot_harness(tmp_path, state, {"widget_family": "systemMedium"})
    harness_text = harness.read_text()
    assert ".frame(width: 364, height: 170)" in harness_text
    assert "Widget Preview" in harness_text

    small = tmp_path / "widget_full.png"
    Image.new("RGB", (400, 400), "green").save(small)
    output_dir = tmp_path / "presented"
    output_dir.mkdir()
    assert crop_widget_screenshot(small, output_dir, 170, 170)
    presented = output_dir / "widget.png"
    with Image.open(presented) as image:
        assert image.size == (1290, 2796)
    assert not small.exists()

    large = tmp_path / "context_full.png"
    Image.new("RGB", (1290, 2796), "blue").save(large)
    assert crop_widget_screenshot(large, output_dir, 170, 170)
    assert (output_dir / "context.png").is_file()
    assert not large.exists()
    invalid = tmp_path / "invalid_full.png"
    invalid.write_text("not an image")
    assert not crop_widget_screenshot(invalid, output_dir, 170, 170)

    empty_widget_dir = tmp_path / "EmptyWidget"
    empty_widget_dir.mkdir()
    assert generate_widget_sample_data(tmp_path, state, {"extension_dir": empty_widget_dir}) == generic


def test_cached_scenarios_guides_and_ui_target_discovery(tmp_path) -> None:
    scenarios = [
        {
            "name": "01_main",
            "description": "Main reading view",
            "navigation": "Launch",
            "priority": 1,
        },
        {
            "name": "02_stats",
            "description": "Reading statistics",
            "navigation": "Tap stats",
            "priority": 2,
        },
    ]
    project = tmp_path / "Reader.xcodeproj"
    project.mkdir()
    ui_tests = tmp_path / "ReaderUITests"
    ui_tests.mkdir()
    state = ProjectState(app_name="Reader")
    state.metadata["xcode_project"] = str(project)
    state.metadata["screenshot_scenarios"] = scenarios
    assert analyze_screenshot_scenarios(tmp_path, state) == scenarios
    assert generate_screenshot_scenarios(tmp_path, state) == [
        "Main reading view",
        "Reading statistics",
    ]
    assert find_ui_test_target(tmp_path, state) == (True, "ReaderUITests")
    ui_tests.rmdir()
    assert find_ui_test_target(tmp_path, state) == (False, "")
    assert find_ui_test_target(tmp_path, ProjectState()) == (False, "")
    assert create_manual_capture_instructions(tmp_path, state)
    guide = tmp_path / "fastlane" / "screenshots" / "en-US" / "README.md"
    assert "Main reading view" in guide.read_text()
    assert run_fastlane_snapshot(tmp_path)

    empty_project = tmp_path / "empty"
    empty_project.mkdir()
    defaults = analyze_screenshot_scenarios(empty_project, ProjectState(app_name="Empty"))
    assert len(defaults) == 5
    assert defaults[0]["name"] == "01_main"
