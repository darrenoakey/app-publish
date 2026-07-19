import sys
from io import StringIO

from manual_screenshots import (
    DEVICES,
    build_device_capture_plan,
    execute_capture_plan,
    main,
    screenshot_output_path,
    select_screenshot_devices,
    wait_for_enter,
)
from state import ProjectState, save_state


def test_device_catalog_and_output_paths(tmp_path) -> None:
    assert len({device["name"] for device in DEVICES}) == 4
    assert all(device["name"] == device["prefix"] for device in DEVICES)
    assert screenshot_output_path(DEVICES[0], tmp_path, "01_main") == tmp_path / "iPhone 16 Pro Max-01_main.png"
    plan = build_device_capture_plan(
        DEVICES[0],
        tmp_path,
        "com.example.reader",
        [{"name": "01_main", "description": "Main", "navigation": "Launch"}],
    )
    commands = [action[1] for action in plan if action[0] == "command"]
    capture_action = next(action for action in plan if action[0] == "capture")
    assert commands[0] == ["xcrun", "simctl", "boot", "iPhone 16 Pro Max"]
    assert commands[-1] == ["xcrun", "simctl", "shutdown", "iPhone 16 Pro Max"]
    assert capture_action[2] == tmp_path / "iPhone 16 Pro Max-01_main.png"
    assert "Navigation: Launch" in capture_action[3]
    assert (
        execute_capture_plan(
            [
                ("info", "ready"),
                ("command", [sys.executable, "-c", "pass"]),
                ("wait", 0),
            ]
        )
        == 0
    )
    assert select_screenshot_devices("all") == DEVICES
    assert select_screenshot_devices("2, 4") == [DEVICES[1], DEVICES[3]]
    assert select_screenshot_devices("invalid") == DEVICES[:1]
    assert select_screenshot_devices("99") == DEVICES[:1]


def test_enter_prompt_consumes_a_real_input_stream(capsys) -> None:
    original = sys.stdin
    try:
        sys.stdin = StringIO("\n")
        wait_for_enter("Ready")
    finally:
        sys.stdin = original
    assert "Ready" in capsys.readouterr().out


def test_main_rejects_missing_project_argument_and_bundle_identifier(tmp_path) -> None:
    save_state(tmp_path, ProjectState(project_path=str(tmp_path), project_name=tmp_path.name))
    original = sys.argv
    try:
        sys.argv = ["manual_screenshots.py"]
        assert main() == 1
        sys.argv = ["manual_screenshots.py", str(tmp_path)]
        assert main() == 1
    finally:
        sys.argv = original
