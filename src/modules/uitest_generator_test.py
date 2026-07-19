import importlib
import json

import modules.uitest_generator as uitest_generator_module
from modules.uitest_generator import (
    generate_snapfile,
    generate_uitest_swift_code,
    get_default_analysis,
    run,
)

importlib.reload(uitest_generator_module)


def test_ui_test_generation_orders_scenarios_and_translates_actions(tmp_path) -> None:
    analysis = get_default_analysis()
    analysis["test_scenarios"] = [
        {
            "name": "search-results",
            "description": "Results",
            "screenshot_name": "02_results",
            "priority": 2,
            "setup_steps": [{"action": "type", "target": "query", "value": "ocean"}],
        },
        {
            "name": "welcome",
            "description": "Welcome",
            "screenshot_name": "01_welcome",
            "priority": 1,
            "setup_steps": [{"action": "swipe", "value": "up"}],
        },
    ]
    swift = generate_uitest_swift_code(analysis, "com.example.reader")
    assert swift.index("testWelcome") < swift.index("testSearchResults")
    assert 'app.textFields["query"]' in swift
    assert 'textField.typeText("ocean")' in swift
    assert "app.swipeUp()" in swift
    snapfile = generate_snapfile(tmp_path, [{"name": "Phone"}, {"name": "Tablet"}])
    assert '"Phone"' in snapfile and '"Tablet"' in snapfile


def test_ui_test_generation_writes_complete_real_local_project_artifacts(
    tmp_path,
) -> None:
    analysis = run(tmp_path, "com.example.reader")
    stored = json.loads((tmp_path / "fastlane" / "screenshot_analysis.json").read_text())
    assert stored == analysis == get_default_analysis()
    tests = tmp_path / "ios" / "App" / "AppUITests"
    assert "class ScreenshotUITests" in (tests / "ScreenshotUITests.swift").read_text()
    assert "CFBundleIdentifier" in (tests / "Info.plist").read_text()
    assert "open class Snapshot" in (tests / "SnapshotHelper.swift").read_text()
    assert (tmp_path / "fastlane" / "Snapfile").is_file()
    assert not (tmp_path / "add_uitest_target.rb").exists()
