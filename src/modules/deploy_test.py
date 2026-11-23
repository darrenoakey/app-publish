import importlib
import json

import modules.deploy as deploy_module
from modules.deploy import (
    build_for_device,
    detect_bundle_id,
    detect_project_type,
    ensure_dev_profile,
    find_connected_devices,
    install_attempt_sequence,
    install_command_attempts,
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
                    "deviceProperties": {
                        "name": "Apollo",
                        "marketingName": "iPhone 16 Pro",
                        "osVersionNumber": "26.3",
                    },
                },
                {
                    "identifier": "simulator-id",
                    "connectionProperties": {"transportType": "sameMachine"},
                    "deviceProperties": {"name": "iPhone 17 Pro"},
                },
            ]
        }
    }
    parsed = parse_devicectl_devices(json.dumps(devicectl))
    # A phone paired over Wi-Fi reports localNetwork and is still a real,
    # installable device; dropping it made Apollo look absent.
    assert [device["id"] for device in parsed] == ["wired-id", "wifi-id"]
    assert parsed[1]["name"] == "Apollo"
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
    assert sync_web_content(tmp_path) is False
    assert install_on_device(str(tmp_path / "Missing.app"), "missing-device-id") is False


def test_install_command_attempts_put_devicectl_before_ios_deploy() -> None:
    attempts = install_command_attempts("/tmp/App.app", "some-device-id")
    assert len(attempts) == 2
    assert "devicectl" in attempts[0][1]
    assert "--device" in attempts[0]
    assert "some-device-id" in attempts[0]
    assert attempts[1][0] == "ios-deploy"
    assert "--id" in attempts[1]


def test_install_command_attempts_work_without_a_device_id() -> None:
    attempts = install_command_attempts("/tmp/App.app")
    assert len(attempts) == 2
    assert "--device" not in attempts[0]
    assert "--id" not in attempts[1]


def test_connected_device_discovery_uses_the_real_apple_toolchain() -> None:
    devices = find_connected_devices()
    assert isinstance(devices, list)
    for device in devices:
        assert set(device) == {"id", "name", "model", "os"}
        assert device["id"]
        assert device["name"]


def test_devicectl_install_retries_keep_the_exact_target_and_artifact() -> None:
    path = "/tmp/App.app"
    device = "some-device-id"
    devicectl, ios_deploy = install_command_attempts(path, device)

    assert install_attempt_sequence(path, device) == [devicectl, devicectl, devicectl, ios_deploy]


def test_bundle_id_is_detected_from_a_nested_ios_project_yml(tmp_path) -> None:
    # agentd3-ios keeps its XcodeGen spec under ios/, not the repo root, so
    # root-only detection reported "Could not detect bundle ID".
    ios = tmp_path / "ios"
    ios.mkdir()
    (ios / "project.yml").write_text(
        """name: agentd3-ios
targets:
  agentd3-iosTests:
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.darrenoakey.agentd3.tests
  agentd3-ios:
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.darrenoakey.agentd3
"""
    )

    assert detect_bundle_id(tmp_path) == "com.darrenoakey.agentd3"


def test_bundle_id_prefers_the_app_target_over_a_test_target(tmp_path) -> None:
    # A spec may declare a test target first; signing the test runner would be
    # wrong, so the app identifier wins.
    (tmp_path / "project.yml").write_text(
        """targets:
  AppUITests:
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.darrenoakey.thing.uitests
  App:
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.darrenoakey.thing
"""
    )

    assert detect_bundle_id(tmp_path) == "com.darrenoakey.thing"


def test_bundle_id_is_none_when_no_project_metadata_exists(tmp_path) -> None:
    assert detect_bundle_id(tmp_path) is None


def test_native_project_under_ios_dir_is_detected(tmp_path) -> None:
    # agentd3-ios keeps ios/agentd3-ios.xcodeproj rather than a root project,
    # so root-only detection reported "No Xcode project found".
    ios = tmp_path / "ios"
    project = ios / "agentd3-ios.xcodeproj"
    project.mkdir(parents=True)

    kind, xcodeproj, scheme = detect_project_type(tmp_path)

    assert kind == "native"
    assert xcodeproj == project
    assert scheme == "agentd3-ios"
