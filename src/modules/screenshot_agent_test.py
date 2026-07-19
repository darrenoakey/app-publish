import importlib
import json

import modules.screenshot_agent as screenshot_agent_module
from modules.screenshot_agent import (
    analyze_app_structure,
    build_simulator_app,
    generate_automation_script,
    read_source_snippets,
    run,
)

importlib.reload(screenshot_agent_module)


def test_automation_script_embeds_scenarios_and_navigation(tmp_path) -> None:
    analysis = {
        "screenshot_scenarios": [{"name": "details", "setup_steps": ["click #details"]}],
        "navigation": [{"from": "main", "to": "details", "action": "click #details"}],
    }
    path = generate_automation_script(analysis, tmp_path)
    content = path.read_text()
    assert json.dumps(analysis["screenshot_scenarios"], indent=2) in content
    assert '"main_to_details": "click #details"' in content
    assert "window.startScreenshotAutomation" in content


def test_source_analysis_reads_bounded_real_local_files(tmp_path, capsys) -> None:
    (tmp_path / "index.html").write_text("<main>Reader</main>")
    (tmp_path / "app.js").write_text("const title = 'Reader';")
    (tmp_path / "large.ts").write_text("x" * 100001)
    snippets = read_source_snippets(tmp_path)
    assert any(snippet.startswith("=== app.js ===") for snippet in snippets)
    assert any("<main>Reader</main>" in snippet for snippet in snippets)
    assert all("large.ts" not in snippet for snippet in snippets)
    assert read_source_snippets(tmp_path, max_size=1) == []

    empty = tmp_path / "empty"
    empty.mkdir()
    assert analyze_app_structure(empty) == {
        "screens": [],
        "navigation": [],
        "elements": [],
    }
    assert "No analyzable source files found" in capsys.readouterr().out


def test_simulator_build_reports_real_local_xcode_failure(tmp_path) -> None:
    assert build_simulator_app(tmp_path) is None


def test_screenshot_run_persists_script_before_real_local_build_failure(
    tmp_path,
) -> None:
    command_directory = tmp_path / "node_modules" / ".bin"
    command_directory.mkdir(parents=True)
    (command_directory / "cap").symlink_to("/usr/bin/false")
    assert run(tmp_path, "com.example.reader") is False
    assert (tmp_path / "screenshot-automation.js").is_file()
    assert (tmp_path / "fastlane" / "screenshots" / "en-US").is_dir()
