"""Real provider and Apple API coverage for the no-Keychain signing path."""

import plistlib
import shutil
import subprocess

import apple_portal
from config import TEAM_ID
from modules.signing import (
    ensure_bundle_id,
    ensure_distribution_cert,
    has_distribution_cert,
    run,
    sign_app_bundle,
)
from state import ProjectState


def test_existing_bundle_id_is_verified_through_real_apple_api() -> None:
    assert ensure_bundle_id("com.darrenoakey.olBridge", "OL Bridge")


def test_distribution_identity_state_comes_from_real_encrypted_provider() -> None:
    expected = apple_portal.secrets_store.get_secret_bytes(apple_portal.SERVICE, apple_portal.P12_ACCOUNT) is not None
    assert has_distribution_cert() is expected


def test_real_bundle_is_profiled_and_signed_without_keychain(tmp_path) -> None:
    bundle_id = "com.darrenoakey.olBridge"
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
    assert sign_app_bundle(app, bundle_id, "OL Bridge", "IOS_APP_STORE")
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
