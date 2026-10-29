"""Real provider and Apple API coverage for the no-Keychain signing path."""

import base64
import plistlib
import shutil
import subprocess

import pytest

import apple_portal
from config import TEAM_ID
from modules.signing import (
    _profile_entitlements,
    _select_entitlements,
    ensure_bundle_id,
    ensure_distribution_cert,
    has_distribution_cert,
    requested_entitlements,
    run,
    sign_app_bundle,
)
from state import ProjectState


def test_real_groupiecam_profile_drops_only_unrequested_game_center() -> None:
    profiles = apple_portal.request(
        "GET",
        "bundleIds/6ALWCPQYSL/profiles",
        params={"limit": 200},
    )["data"]
    app_store_profiles = [item for item in profiles if item.get("attributes", {}).get("profileType") == "IOS_APP_STORE"]
    assert app_store_profiles
    for item in app_store_profiles:
        try:
            profile = apple_portal.request("GET", f"profiles/{item['id']}")
        except apple_portal.ApplePortalError as error:
            if error.status_code == 404:
                continue
            raise
        content = base64.b64decode(profile["data"]["attributes"]["profileContent"])
        authorized = plistlib.loads(_profile_entitlements(content, {"com.apple.developer.game-center": True}))
        if authorized.get("com.apple.developer.game-center") is True:
            break
    else:
        raise AssertionError("GroupieCam has no readable App Store profile with its legacy Game Center capability")

    selected = plistlib.loads(_profile_entitlements(content))

    assert "com.apple.developer.game-center" not in selected
    assert selected == {key: value for key, value in authorized.items() if key != "com.apple.developer.game-center"}


def test_requested_capabilities_preserve_exact_values_within_profile_authorization() -> None:
    profile = {
        "application-identifier": f"{TEAM_ID}.darrenoakey.Capabilities",
        "aps-environment": "production",
        "com.apple.developer.associated-domains": ["*"],
        "com.apple.developer.game-center": True,
        "com.apple.developer.icloud-container-identifiers": [
            "iCloud.darrenoakey.Capabilities",
            "iCloud.darrenoakey.Unrequested",
        ],
        "com.apple.developer.team-identifier": TEAM_ID,
        "com.apple.developer.usernotes": True,
        "com.apple.security.application-groups": [
            f"{TEAM_ID}.group.capabilities",
            f"{TEAM_ID}.group.unrequested",
        ],
    }
    requested = {
        "aps-environment": "production",
        "com.apple.developer.associated-domains": ["applinks:*.example.com?mode=developer"],
        "com.apple.developer.icloud-container-identifiers": ["iCloud.darrenoakey.Capabilities"],
        "com.apple.security.application-groups": [f"{TEAM_ID}.group.capabilities"],
    }

    selected = _select_entitlements(profile, requested)

    assert selected["aps-environment"] == "production"
    assert selected["com.apple.developer.associated-domains"] == requested["com.apple.developer.associated-domains"]
    assert (
        selected["com.apple.developer.icloud-container-identifiers"]
        == requested["com.apple.developer.icloud-container-identifiers"]
    )
    assert selected["com.apple.security.application-groups"] == requested["com.apple.security.application-groups"]
    assert "com.apple.developer.game-center" not in selected
    assert "com.apple.developer.usernotes" not in selected
    with pytest.raises(apple_portal.ApplePortalError, match="aps-environment"):
        _select_entitlements(profile, {**requested, "aps-environment": "development"})
    with pytest.raises(apple_portal.ApplePortalError, match="aps-environment"):
        _select_entitlements({"application-identifier": profile["application-identifier"]}, requested)


def test_requested_prefix_variables_resolve_from_real_profile_document() -> None:
    document = {
        "ApplicationIdentifierPrefix": ["PREFIX123"],
        "TeamIdentifier": [TEAM_ID],
    }
    profile = {
        "application-identifier": "PREFIX123.*",
        "com.apple.developer.team-identifier": TEAM_ID,
        "keychain-access-groups": [f"{TEAM_ID}.*"],
    }
    requested = {
        "application-identifier": "$(AppIdentifierPrefix)darrenoakey.Capabilities",
        "keychain-access-groups": ["$(TeamIdentifierPrefix)group.capabilities"],
    }

    selected = _select_entitlements(profile, requested, document)

    assert selected == {
        "application-identifier": "PREFIX123.darrenoakey.Capabilities",
        "com.apple.developer.team-identifier": TEAM_ID,
        "keychain-access-groups": [f"{TEAM_ID}.group.capabilities"],
    }
    with pytest.raises(apple_portal.ApplePortalError, match="unresolved variable"):
        _select_entitlements(profile, {"application-identifier": "$(UnknownPrefix)darrenoakey.Capabilities"}, document)


def test_requested_entitlements_come_from_real_xcode_targets(tmp_path) -> None:
    bundle_id = "darrenoakey.EntitlementFixture"
    extension_bundle_id = "darrenoakey.EntitlementFixture.Extension"
    requested = {
        "aps-environment": "production",
        "com.apple.developer.icloud-container-identifiers": ["iCloud.darrenoakey.EntitlementFixture"],
        "com.apple.security.application-groups": [f"{TEAM_ID}.group.entitlement-fixture"],
    }
    extension_requested = {"aps-environment": "development"}
    (tmp_path / "Entitlements.plist").write_bytes(plistlib.dumps(requested))
    (tmp_path / "ExtensionEntitlements.plist").write_bytes(plistlib.dumps(extension_requested))
    (tmp_path / "project.yml").write_text(
        f"""name: EntitlementFixture
options:
  bundleIdPrefix: darrenoakey
targets:
  EntitlementFixture:
    type: application
    platform: iOS
    sources: []
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: {bundle_id}
        CODE_SIGN_ENTITLEMENTS: Entitlements.plist
    dependencies:
      - target: EntitlementFixtureExtension
  EntitlementFixtureExtension:
    type: app-extension
    platform: iOS
    sources: []
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: {extension_bundle_id}
        CODE_SIGN_ENTITLEMENTS: ExtensionEntitlements.plist
"""
    )
    subprocess.run(
        ["xcodegen", "generate"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    )

    resolved = requested_entitlements(
        str(tmp_path / "EntitlementFixture.xcodeproj"),
        "EntitlementFixture",
        "Release",
        use_workspace=False,
    )

    assert resolved == {bundle_id: requested, extension_bundle_id: extension_requested}


def test_real_bundle_is_profiled_and_signed_without_keychain(tmp_path) -> None:
    bundle_id = "com.darrenoakey.olBridge"
    assert ensure_bundle_id(bundle_id, "OL Bridge")

    expected = apple_portal.secrets_store.get_secret_bytes(apple_portal.SERVICE, apple_portal.P12_ACCOUNT) is not None
    assert has_distribution_cert() is expected

    app = tmp_path / "OLBridge.app"
    app.mkdir()
    executable = app / "OLBridge"
    shutil.copyfile("/usr/bin/true", executable)
    executable.chmod(0o755)
    (app / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleExecutable": executable.name,
                "CFBundleIdentifier": bundle_id,
                "CFBundleName": "OL Bridge",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": "1.0",
                "CFBundleVersion": "1",
            }
        )
    )

    assert ensure_distribution_cert()
    assert sign_app_bundle(app, bundle_id, "OL Bridge", "IOS_APP_STORE", requested={bundle_id: {}})
    assert (app / "embedded.mobileprovision").is_file()

    # Regression: rcodesign `main:<path>` scope drops entitlements; the main
    # bundle must carry application-identifier or device install rejects it.
    dumped = subprocess.check_output(
        ["codesign", "-d", "--entitlements", ":-", "--xml", str(app)],
        stderr=subprocess.DEVNULL,
    )
    ents = plistlib.loads(dumped)
    assert ents.get("application-identifier") == f"{TEAM_ID}.{bundle_id}"
    assert ents.get("com.apple.developer.team-identifier") == TEAM_ID

    state = ProjectState(bundle_id=bundle_id, project_name="OL Bridge")
    assert run(tmp_path, state)
    assert state.metadata["signing_configured"] is True
