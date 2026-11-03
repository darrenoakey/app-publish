import pytest

from modules.device_guard import (
    RELEASE,
    STARBUCK_COREDEVICE_ID,
    STARBUCK_UDID,
    ProtectedDeviceError,
    assert_release_only,
    assert_test_destination,
)

SIM = "platform=iOS Simulator,id=B80A199E-B118-4365-8CBC-581D758FFCB9"


@pytest.mark.parametrize(
    "dest",
    [
        f"id={STARBUCK_UDID}",
        "platform=iOS,name=Starbuck",
        "name=Starbuck",
        "Starbuck (27.2)",
        "platform=iOS Simulator,id=B80A199E-B118-4365-8CBC-581D758FFCB9,name=Starbuck",
    ],
)
def test_starbuck_refused_for_tests(dest: str) -> None:
    with pytest.raises(ProtectedDeviceError):
        assert_test_destination(dest)


@pytest.mark.parametrize(
    "dest",
    [
        "generic/platform=iOS",
        "platform=iOS,id=00000000-0000-0000-0000-000000000000",
        "iPhone 17 Pro Max",
    ],
)
def test_physical_or_unknown_device_refused_for_tests(dest: str) -> None:
    with pytest.raises(ProtectedDeviceError):
        assert_test_destination(dest)


def test_simulator_destination_allowed() -> None:
    assert_test_destination("platform=iOS Simulator,id=B80A199E-B118-4365-8CBC-581D758FFCB9")


@pytest.mark.parametrize("purpose", ["test", "record", "screenshot", "timing", "device-state", "location", ""])
def test_starbuck_refused_for_non_release_purposes(purpose: str) -> None:
    with pytest.raises(ProtectedDeviceError):
        assert_release_only(["Starbuck"], purpose)


def test_release_install_on_starbuck_still_allowed() -> None:
    assert_release_only(["Starbuck", STARBUCK_UDID], RELEASE)


def test_bare_device_name_fails_closed_for_non_release() -> None:
    # Allowlist: a bare device name is not a proven simulator destination.
    with pytest.raises(ProtectedDeviceError):
        assert_release_only(["iPad Pro 13-inch (M4)"], "test")


def test_screenshot_and_deploy_wiring_refuses_starbuck_for_screenshots() -> None:
    # Screenshot capture paths call assert_release_only with purpose "screenshot".
    with pytest.raises(ProtectedDeviceError):
        assert_release_only(["Starbuck"], "screenshot")


def test_deploy_path_release_check_allows_default_starbuck() -> None:
    # deploy.run default device is Starbuck; that is a release install and must pass.
    assert_release_only(["Starbuck"], RELEASE)


def test_screenshot_agent_entry_refuses_starbuck_before_running_anything(tmp_path) -> None:
    from pathlib import Path
    from modules import screenshot_agent

    with pytest.raises(ProtectedDeviceError):
        screenshot_agent.capture_device_screenshots(
            {"name": "Starbuck", "suffix": "iphone67", "display": "6.7 inch"},
            "com.example.app",
            Path(tmp_path) / "missing.app",
            [],
            Path(tmp_path),
        )


def test_screenshot_automation_entry_refuses_starbuck_before_running_anything(tmp_path) -> None:
    from pathlib import Path
    from modules import screenshot_automation

    with pytest.raises(ProtectedDeviceError):
        screenshot_automation.capture_scenario(
            {"name": "Starbuck", "suffix": "iphone67", "size": "6.7 inch"},
            {"name": "Scenario"},
            Path(tmp_path),
            "com.example.app",
            Path(tmp_path) / "missing.app",
        )


# --- direct general-guard regression: every Starbuck alias and unknown targets ---

STARBUCK_TARGETS = [
    STARBUCK_UDID,
    STARBUCK_COREDEVICE_ID,
    STARBUCK_UDID.lower(),
    STARBUCK_COREDEVICE_ID.lower(),
    STARBUCK_UDID.replace("-", ""),
    STARBUCK_COREDEVICE_ID.replace("-", ""),
    "Starbuck",
    "starbuck",
    "STARBUCK",
    "Starbuck (27.2)",
    f"id={STARBUCK_COREDEVICE_ID}",
    f"platform=iOS,id={STARBUCK_COREDEVICE_ID}",
    f"platform=iOS,id={STARBUCK_UDID}",
    f"platform=iOS Simulator,id={STARBUCK_COREDEVICE_ID}",
    "platform=iOS Simulator,name=Starbuck",
]

NON_SIMULATOR_TARGETS = [
    "generic/platform=iOS",
    "platform=iOS,id=00000000-0000-0000-0000-000000000000",
    "iPhone 17 Pro Max",
    "BD3CD332-2BB1-5F19-B4ED-953A902E7417X",
    "",
]


@pytest.mark.parametrize("target", STARBUCK_TARGETS)
@pytest.mark.parametrize("purpose", ["test", "record", "screenshot", "timing", "video", "device-state"])
def test_general_guard_refuses_every_starbuck_alias(target: str, purpose: str) -> None:
    with pytest.raises(ProtectedDeviceError):
        assert_release_only([target], purpose)


@pytest.mark.parametrize("target", STARBUCK_TARGETS)
def test_test_destination_refuses_every_starbuck_alias(target: str) -> None:
    with pytest.raises(ProtectedDeviceError):
        assert_test_destination(target)


@pytest.mark.parametrize("target", NON_SIMULATOR_TARGETS)
def test_general_guard_fails_closed_on_unknown_or_non_simulator(target: str) -> None:
    with pytest.raises(ProtectedDeviceError):
        assert_release_only([target], "test")
    with pytest.raises(ProtectedDeviceError):
        assert_test_destination(target)


def test_general_guard_allows_identified_simulator_for_non_release() -> None:
    assert_release_only([SIM], "test")
    assert_release_only([SIM], "screenshot")
    assert_test_destination(SIM)


def test_release_purpose_still_allows_starbuck_by_every_alias() -> None:
    for target in STARBUCK_TARGETS:
        assert_release_only([target], RELEASE)


# --- fail-closed validation: empty, malformed, ambiguous, unknown, conflicting ---

from modules.device_guard import _main, is_simulator_destination, validate_simulator_destination  # noqa: E402

SIM_ID = "B80A199E-B118-4365-8CBC-581D758FFCB9"

BAD_DESTINATIONS = [
    "",
    "   ",
    ",",
    "platform=iOS Simulator",  # no id/name
    "platform=iOS Simulator,",  # trailing empty field
    "platform=iOS Simulator,,name=iPhone 17",  # empty field
    "platform=iOS Simulator,name=",  # empty value
    "platform=iOS Simulator,=iPhone 17",  # empty key
    "platform iOS Simulator name iPhone",  # no '='
    "platform=iOS Simulator,name=iPhone 17,unknown=1",  # unknown key
    "Platform=iOS Simulator,name=iPhone 17",  # key case
    "platform=iOS Simulator,name=iPhone 17,name=iPhone 16",  # duplicate key
    f"platform=iOS Simulator,id={SIM_ID},id={SIM_ID}",  # duplicate id
    f"platform=iOS Simulator,id={SIM_ID},name=iPhone 17",  # id and name both (ambiguous)
    "platform=iOS,platform=iOS Simulator,name=iPhone 17",  # conflicting platform
    "platform=iOS Simulator,platform=iOS,name=iPhone 17",  # conflicting platform reversed
    "platform=iOS Simulator,name=iPhone 17,platform=iOS",  # conflicting platform trailing
    "platform=iOS Simulator Duo,name=iPhone Duo",  # not exactly iOS Simulator
    "platform=ios simulator,name=iPhone 17",  # wrong case platform value
    "platform=macOS,name=iPhone 17",  # wrong platform
    "name=iPhone 17",  # simulator marker missing
    "platform=iOS",  # physical marker, no simulator
    "platform=iOS,id=00000000-0000-0000-0000-000000000000",  # physical with id
    "platform=iOS Simulator,generic/platform=iOS",  # generic inside a simulator string
    "generic/platform=iOS Simulator",  # generic destination
    "platform=iOS Simulator,name=iPhone 17 generic",  # generic word refused
    "platform=iOS Simulator,id=",  # empty id
    "platform=iOS Simulator,name=   ",  # whitespace-only name
    "Simulator",  # bare word
    "simulator-platform=iOS Simulator,name=iPhone 17",  # unknown key containing simulator
    "platform=iOS Simulator,name=iPhone 17,os=",  # empty os value
    "platform=iOS Simulator,name=iPhone 17,os=26.2,os=27.0",  # duplicate os
    "platform=iOS Simulator,name=Starbuck",  # Starbuck name in simulator form
    f"platform=iOS Simulator,id={STARBUCK_COREDEVICE_ID}",  # Starbuck CoreDevice id
    f"platform=iOS Simulator,id={STARBUCK_UDID}",  # Starbuck hardware id
]


@pytest.mark.parametrize("dest", BAD_DESTINATIONS)
def test_malformed_ambiguous_or_unknown_destination_refused(dest: str) -> None:
    with pytest.raises(ProtectedDeviceError):
        validate_simulator_destination(dest)
    with pytest.raises(ProtectedDeviceError):
        assert_test_destination(dest)
    assert not is_simulator_destination(dest)


@pytest.mark.parametrize("dest", BAD_DESTINATIONS)
def test_non_release_purposes_refuse_bad_destinations(dest: str) -> None:
    for purpose in ["test", "record", "timing", "video", "screenshot"]:
        with pytest.raises(ProtectedDeviceError):
            assert_release_only([dest], purpose)


def test_empty_identifier_list_refused_for_every_purpose() -> None:
    for purpose in ["test", "record", "timing", "video", "screenshot", RELEASE]:
        with pytest.raises(ProtectedDeviceError):
            assert_release_only([], purpose)


def test_empty_identifier_entry_refused_for_every_purpose() -> None:
    for purpose in ["test", RELEASE]:
        with pytest.raises(ProtectedDeviceError):
            assert_release_only([""], purpose)
        with pytest.raises(ProtectedDeviceError):
            assert_release_only(["   "], purpose)


def test_one_bad_destination_in_list_refuses_whole_list() -> None:
    with pytest.raises(ProtectedDeviceError):
        assert_release_only([f"platform=iOS Simulator,id={SIM_ID}", "platform=iOS,id=1"], "test")


@pytest.mark.parametrize(
    "dest",
    [
        f"platform=iOS Simulator,id={SIM_ID}",
        "platform=iOS Simulator,name=iPhone 17",
        "platform=iOS Simulator,name=iPhone 16 Pro Max,os=26.2",
        "platform=iOS Simulator,id=" + SIM_ID + ",os=26.2",
    ],
)
def test_well_formed_simulator_destinations_accepted(dest: str) -> None:
    validate_simulator_destination(dest)
    assert_release_only([dest], "test")
    assert_release_only([dest], "screenshot")


def test_release_purpose_still_targets_starbuck_when_well_formed_list() -> None:
    assert_release_only(["Starbuck"], RELEASE)
    assert_release_only([STARBUCK_COREDEVICE_ID], RELEASE)


def test_cli_exit_codes_match_guard(capsys) -> None:
    assert _main(["test-destination", f"platform=iOS Simulator,id={SIM_ID}"]) == 0
    assert _main(["test-destination", f"platform=iOS Simulator,id={STARBUCK_COREDEVICE_ID}"]) == 3
    assert _main(["test-destination", "platform=iOS,id=1"]) == 3
    assert _main(["test-destination"]) == 3
    assert _main(["test-destination", ""]) == 3
    assert _main(["bogus", "platform=iOS Simulator,name=iPhone 17"]) == 3
    assert _main(["test-destination", "platform=iOS Simulator,name=iPhone 17", "platform=iOS,id=1"]) == 3


# --- documented Xcode destination syntax (uppercase OS, arch) stays accepted; bad forms stay refused ---

VALID_DOCUMENTED = [
    "platform=iOS Simulator,name=iPhone 17 Pro,OS=26.2",
    "platform=iOS Simulator,name=iPhone 17 Pro,OS=26.2,arch=arm64",
    "platform=iOS Simulator,name=iPhone 17,arch=arm64,OS=26.2",
    "platform=iOS Simulator,name=iPhone 16 Pro Max,OS=latest",
    f"platform=iOS Simulator,id={SIM_ID},OS=26.2",
    f"platform=iOS Simulator,id={SIM_ID},OS=26.2,arch=x86_64",
    "platform=iOS Simulator,name=iPhone 17,os=26.2",
    "platform=iOS Simulator,name=iPhone 17,OS=27.0.1",
]

INVALID_DOCUMENTED = [
    "platform=iOS Simulator,name=iPhone 17,OS=26.2,os=27.0",  # duplicate OS across spellings
    "platform=iOS Simulator,name=iPhone 17,OS=26.2,OS=26.2",  # duplicate OS
    "platform=iOS Simulator,name=iPhone 17,OS=",  # empty OS
    "platform=iOS Simulator,name=iPhone 17,OS=bogus",  # malformed OS value
    "platform=iOS Simulator,name=iPhone 17,OS=26.2;rm",  # injection-shaped OS value
    "platform=iOS Simulator,name=iPhone 17,arch=ppc",  # unknown arch
    "platform=iOS Simulator,name=iPhone 17,arch=arm64,arch=arm64",  # duplicate arch
    "platform=iOS Simulator,name=iPhone 17,ARCH=arm64",  # unknown key case
    "Platform=iOS Simulator,name=iPhone 17,OS=26.2",  # key case on platform
    "platform=iOS Simulator,name=Starbuck,OS=26.2",  # Starbuck with valid OS/arch
    f"platform=iOS Simulator,id={STARBUCK_COREDEVICE_ID},OS=26.2,arch=arm64",  # Starbuck id
    "platform=iOS,name=iPhone 17,OS=26.2,arch=arm64",  # physical platform with documented keys
    "generic/platform=iOS Simulator,OS=26.2",  # generic
]


@pytest.mark.parametrize("dest", VALID_DOCUMENTED)
def test_documented_uppercase_os_and_arch_accepted(dest: str) -> None:
    validate_simulator_destination(dest)
    assert_release_only([dest], "test")
    assert_test_destination(dest)


@pytest.mark.parametrize("dest", INVALID_DOCUMENTED)
def test_documented_syntax_with_bad_values_still_refused(dest: str) -> None:
    with pytest.raises(ProtectedDeviceError):
        validate_simulator_destination(dest)
    with pytest.raises(ProtectedDeviceError):
        assert_release_only([dest], "record")


def test_cli_accepts_documented_destination(capsys) -> None:
    assert _main(["test-destination", "platform=iOS Simulator,name=iPhone 17 Pro,OS=26.2,arch=arm64"]) == 0
    assert _main(["test-destination", "platform=iOS Simulator,name=iPhone 17,OS=26.2,os=27.0"]) == 3
