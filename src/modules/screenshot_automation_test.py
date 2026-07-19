import importlib

import modules.screenshot_automation as screenshot_automation_module
from config import STATE_FILE
from modules.screenshot_automation import (
    DEVICES,
    capture_all_devices,
    capture_scenario,
    capture_screenshot,
    get_scenarios,
    inject_javascript,
    install_app,
    launch_app,
    run,
)

importlib.reload(screenshot_automation_module)


def test_device_catalog_and_failed_simulator_capture(tmp_path) -> None:
    assert {device["suffix"] for device in DEVICES} == {
        "iphone67",
        "iphone65",
        "ipad129",
        "ipad11",
    }
    target = tmp_path / "shot.png"
    assert not capture_screenshot("app-publish-device-that-does-not-exist", target)
    assert not target.exists()


def test_local_scenario_state_and_missing_simulator_app_stop_cleanly(tmp_path, capsys) -> None:
    scenarios = get_scenarios(tmp_path)
    assert scenarios
    assert (tmp_path / STATE_FILE).is_file()
    assert run(tmp_path, "com.example.reader") is False
    assert "No simulator app found" in capsys.readouterr().out


def test_invalid_local_simulator_commands_and_empty_capture_plan(tmp_path) -> None:
    device = "app-publish-device-that-does-not-exist"
    app_path = tmp_path / "Reader.app"
    assert install_app(device, app_path) is False
    assert launch_app(device, "com.example.reader") is False
    assert inject_javascript(device, "com.example.reader", "window.location.href") is True
    assert capture_all_devices(tmp_path, "com.example.reader", app_path, []) == 0
    scenario = {"name": "main", "description": "Main screen", "wait": 0}
    assert (
        capture_scenario(
            {"name": device, "suffix": "local"},
            scenario,
            tmp_path,
            "com.example.reader",
            app_path,
        )
        is False
    )
