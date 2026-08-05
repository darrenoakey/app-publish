import importlib
import json

import modules.deploy as deploy_module
from modules.deploy import (
    build_for_device,
    detect_bundle_id,
    detect_project_type,
    ensure_dev_profile,
    find_connected_devices,
    install_on_device,
    parse_devicectl_devices,
    parse_ios_deploy_devices,
    parse_xctrace_devices,
    sync_web_content,
)

importlib.reload(deploy_module)


def test_deploy_detection_reads_capacitor_and_xcodegen_files(tmp_path) -> None:
    (tmp_path / "capacitor.config.json").write_text('{"appId":"com.example.cap"}')
    cap_project = tmp_path / "ios" / "App" / "App.xcodeproj"
    cap_project.mkdir(parents=True)
    assert detect_bundle_id(tmp_path) == "com.example.cap"
    assert detect_project_type(tmp_path) == ("web", cap_project, "App")
    (tmp_path / "capacitor.config.json").unlink()
    (tmp_path / "project.yml").write_text('PRODUCT_BUNDLE_IDENTIFIER: "com.example.yaml"\n')
    assert detect_bundle_id(tmp_path) == "com.example.yaml"
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "capacitor.config.json").write_text("not json")
    assert detect_bundle_id(invalid) is None


def test_device_output_parsers_filter_and_deduplicate_real_command_shapes() -> None:
    xctrace = "\n".join(
        [
            "Starbuck (26.3) (00008150-000611360AC0401C)",
            "iPhone Simulator (18.2) (ABCDEF)",
            "Darren MacBook (15.5) (AABBCC)",
        ]
    )
    assert parse_xctrace_devices(xctrace) == [
        {
            "id": "00008150-000611360AC0401C",
            "name": "Starbuck",
            "model": "iOS Device",
            "os": "26.3",
        }
    ]
    ios_deploy = "\n".join(
        [
            "[....] Found 00008150-000611360AC0401C (N161AP, iPhone 16, USB) 'Starbuck'",
            "[....] Found ABCD1234 (N161AP, iPhone 16, WiFi) 'Arlene'",
            "[....] Found DEAD1234 (Watch, Apple Watch, USB)",
        ]
    )
    assert parse_ios_deploy_devices(ios_deploy, {"00008150-000611360AC0401C"}) == [
        {"id": "ABCD1234", "name": "Arlene", "model": "iPhone 16", "os": ""}
    ]
    devicectl = {
        "result": {
            "devices": [
                {
                    "identifier": "wired-id",
                    "connectionProperties": {"transportType": "wired"},
                    "deviceProperties": {
                        "name": "Starbuck",
                        "marketingName": "iPhone 16",
                        "osVersionNumber": "26.3",
                    },
                },
                {
                    "identifier": "wifi-id",
                    "connectionProperties": {"transportType": "localNetwork"},
                },
            ]
        }
    }
    assert parse_devicectl_devices(json.dumps(devicectl))[0]["id"] == "wired-id"
    assert parse_devicectl_devices("not json") == []


def test_local_deploy_boundaries_fail_before_any_device_change(tmp_path) -> None:
    native = tmp_path / "Reader.xcodeproj"
    native.mkdir()
    assert detect_project_type(tmp_path) == ("native", native, "Reader")
    assert detect_bundle_id(tmp_path) is None
    assert ensure_dev_profile("com.example.reader") is True
    assert (
        build_for_device(
            tmp_path / "missing",
            "com.example.reader",
            "00008150-000611360AC0401C",
        )
        is None
    )

    command_directory = tmp_path / "node_modules" / ".bin"
    command_directory.mkdir(parents=True)
    (command_directory / "cap").symlink_to("/usr/bin/false")
    assert sync_web_content(tmp_path) is True
    assert install_on_device(str(tmp_path / "Missing.app"), "missing-device-id") is False


def test_connected_device_discovery_uses_the_real_apple_toolchain() -> None:
    devices = find_connected_devices()
    assert isinstance(devices, list)
    for device in devices:
        assert set(device) == {"id", "name", "model", "os"}
        assert device["id"]
        assert device["name"]
