# Signing for app-publish.
#
# WE NEVER USE FASTLANE MATCH. Match's encrypted-git-repo model is fragile
# (MATCH_PASSWORD lives in someone's head, machine-to-machine sync is a
# nightmare) and offers nothing over Xcode's built-in automatic signing
# when paired with an App Store Connect API key.
#
# Strategy:
#   1. Verify a distribution cert exists locally (security find-identity).
#      If not, create one via the App Store Connect API and import the
#      resulting .p12 into the login keychain.
#   2. Ensure the Bundle ID exists on the developer portal (idempotent).
#   3. That's it. Profile creation is handled by xcodebuild itself at
#      archive/export time via `-allowProvisioningUpdates` + the API key.
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState
from config import TEAM_ID, API_KEY_PATH, API_KEY_ID, API_ISSUER_ID
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
)


# ##################################################################
# has distribution cert
# True if a usable "Apple Distribution" identity is in the login keychain.
def has_distribution_cert() -> bool:
    ret_code, output = exec_cmd(["security", "find-identity", "-v", "-p", "codesigning"])
    if ret_code != 0:
        return False
    return "Apple Distribution" in output


# ##################################################################
# ensure bundle id
# Ensure the bundle id exists on the developer portal. Idempotent.
def ensure_bundle_id(bundle_id: str, app_name: str) -> bool:
    # Use a small inline spaceship call via the `ruby` binary that ships
    # with fastlane — avoids pulling in a Python ASC client just for one
    # idempotent operation.
    print_info(f"Ensuring Bundle ID {bundle_id} on developer portal...")
    ret_code, output = exec_cmd([
        "ruby", "-rspaceship", "-e",
        f"""
        token = Spaceship::ConnectAPI::Token.create(
          key_id: "{API_KEY_ID}",
          issuer_id: "{API_ISSUER_ID}",
          filepath: "{API_KEY_PATH}",
          in_house: false
        )
        Spaceship::ConnectAPI.token = token
        existing = Spaceship::ConnectAPI::BundleId.all.find {{ |b| b.identifier == "{bundle_id}" }}
        if existing
          puts "EXISTS"
        else
          Spaceship::ConnectAPI::BundleId.create(
            name: "{app_name}",
            identifier: "{bundle_id}",
            platform: Spaceship::ConnectAPI::BundleIdPlatform::IOS
          )
          puts "CREATED"
        end
        """
    ])
    if ret_code != 0:
        print_error(f"Bundle ID check failed: {output}")
        return False
    if "CREATED" in output:
        print_success(f"Created Bundle ID {bundle_id}")
    else:
        print_success(f"Bundle ID {bundle_id} already exists")
    return True


# ##################################################################
# ensure distribution cert
# Create a distribution cert via ASC API if none exists locally.
def ensure_distribution_cert() -> bool:
    if has_distribution_cert():
        print_success("Distribution certificate already present")
        return True

    print_info("No distribution cert found locally; creating one via App Store Connect API...")
    # fastlane's `cert` action handles the create + p12 export + keychain import.
    # `--type appstore` ⇒ Apple Distribution. With `--api_key_path` it talks to
    # ASC directly, no Apple-ID login, no match.
    api_key_json = API_KEY_PATH.parent.parent / "api_key.json"
    ret_code, output = exec_cmd([
        "fastlane", "run", "cert",
        f"api_key_path:{api_key_json}",
        "type:appstore",
        f"team_id:{TEAM_ID}",
        "force:false",
    ], timeout=120)
    if ret_code != 0:
        print_error(f"cert action failed: {output}")
        return False
    if not has_distribution_cert():
        print_error("cert action returned 0 but no distribution cert appeared in keychain")
        return False
    print_success("Distribution certificate created and installed")
    return True


# ##################################################################
# run
# Signing step: ensure bundle id + distribution cert. No match.
def run(project_path: Path, state: ProjectState) -> bool:
    app_name = state.metadata.get("app_name") or state.project_name
    if not ensure_bundle_id(state.bundle_id, app_name):
        return False
    if not ensure_distribution_cert():
        print_warning("Distribution cert missing — archive will fail")
        return False
    state.metadata["signing_configured"] = True
    return True
