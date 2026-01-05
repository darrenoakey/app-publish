from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState
from config import TEAM_ID, CERTIFICATE_REPO, API_KEY_PATH
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    file_exists,
    write_file,
)


# ##################################################################
# ensure api key json
# ensure api key json file exists for fastlane
def ensure_api_key_json() -> Path:
    import json
    from config import API_KEY_ID, API_ISSUER_ID, API_KEY_PATH

    api_key_json = API_KEY_PATH.parent.parent / "api_key.json"

    # fastlane expects either "key" (content) or "key_filepath" (path)
    # some versions prefer the content directly
    if not file_exists(api_key_json) or True:  # always regenerate to be safe
        # read the key content
        key_content = API_KEY_PATH.read_text()

        content = {
            "key_id": API_KEY_ID,
            "issuer_id": API_ISSUER_ID,
            "key": key_content,  # full key content
            "in_house": False,  # not an enterprise account
        }
        api_key_json.write_text(json.dumps(content, indent=2))

    return api_key_json
# ##################################################################
# ensure api key json
# ensure api key json file exists for fastlane


# ##################################################################
# create app id
# create app id on apple developer portal if it doesn't exist
# note: this is now a no-op as match will create the app id when needed
# we just log the intent and return true
def create_app_id(project_path: Path, state: ProjectState) -> bool:
    print_info(f"App ID will be created by match if needed: {state.bundle_id}")
    return True
# ##################################################################
# create app id
# create app id on apple developer portal if it doesn't exist


# ##################################################################
# run fastlane match
# run fastlane match to sync certificates and profiles
def run_fastlane_match(project_path: Path, state: ProjectState, readonly: bool = True) -> bool:
    # ensure api key json exists
    api_key_json = ensure_api_key_json()

    # first try readonly (use existing)
    print_info("Syncing certificates and profiles...")

    match_cmd = [
        "fastlane", "match", "appstore",
        "--app_identifier", state.bundle_id,
        "--team_id", TEAM_ID,
        "--git_url", f"https://github.com/{CERTIFICATE_REPO}",
        "--api_key_path", str(api_key_json),
    ]

    if readonly:
        match_cmd.append("--readonly")

    ret_code, output = exec_cmd(match_cmd, cwd=project_path, timeout=180)

    if ret_code != 0:
        if readonly:
            # try again without readonly (create new if needed)
            print_info("No existing profiles found, creating new ones...")
            return run_fastlane_match(project_path, state, readonly=False)
        else:
            print_error(f"Failed to sync certificates: {output}")
            return False

    print_success("Certificates and profiles synced")
    return True
# ##################################################################
# run fastlane match
# run fastlane match to sync certificates and profiles


# ##################################################################
# verify signing
# verify that signing is properly configured
def verify_signing(project_path: Path, state: ProjectState) -> bool:
    # check for provisioning profiles
    ret_code, output = exec_cmd([
        "security", "find-identity", "-v", "-p", "codesigning"
    ])

    if "Apple Distribution" not in output:
        print_warning("No distribution certificate found")
        return False

    print_success("Distribution certificate available")
    return True
# ##################################################################
# verify signing
# verify that signing is properly configured


# ##################################################################
# run
# run signing step
# syncs certificates and provisioning profiles using fastlane match
def run(project_path: Path, state: ProjectState) -> bool:
    # check if fastlane directory exists
    fastlane_dir = project_path / "fastlane"
    if not fastlane_dir.exists():
        print_error("Fastlane not set up. Run structure step first.")
        return False

    # ensure app id exists on apple developer portal
    if not create_app_id(project_path, state):
        return False

    # check matchfile exists
    matchfile = fastlane_dir / "Matchfile"
    if not file_exists(matchfile):
        print_info("Creating Matchfile...")
        matchfile_content = f'''# Matchfile
git_url("https://github.com/{CERTIFICATE_REPO}")
storage_mode("git")
type("appstore")
app_identifier("{state.bundle_id}")
team_id("{TEAM_ID}")
'''
        matchfile.write_text(matchfile_content)

    # run match
    if not run_fastlane_match(project_path, state):
        return False

    # verify signing is configured
    if not verify_signing(project_path, state):
        print_warning("Signing verification failed, but continuing...")

    state.metadata["signing_configured"] = True
    return True
# ##################################################################
# run
# run signing step
# syncs certificates and provisioning profiles using fastlane match
