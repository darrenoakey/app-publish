# submit module - submits the app for app store review
#
# handles:
# - waiting for build processing
# - selecting the build for submission
# - submitting for review via app store connect api

import time
from threading import Event
import jwt
import requests
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState, load_state, save_state
from config import API_KEY_PATH, API_KEY_ID, API_ISSUER_ID
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    read_file,
)

API_BASE_URL = "https://api.appstoreconnect.apple.com/v1"


# ##################################################################
# get api token
# generates jwt token for app store connect api
def get_api_token() -> str:
    private_key = read_file(API_KEY_PATH)

    header = {"alg": "ES256", "kid": API_KEY_ID, "typ": "JWT"}

    payload = {
        "iss": API_ISSUER_ID,
        "iat": int(time.time()),
        "exp": int(time.time()) + 1200,  # 20 minutes
        "aud": "appstoreconnect-v1",
    }

    return jwt.encode(payload, private_key, algorithm="ES256", headers=header)


# ##################################################################
# get api token
# generates jwt token for app store connect api


# ##################################################################
# api request
# makes a request to app store connect api
def api_request(
    method: str,
    endpoint: str,
    token: str,
    data: dict | None = None,
    base_url: str = API_BASE_URL,
) -> dict | None:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, json=data)
    elif method == "PATCH":
        resp = requests.patch(url, headers=headers, json=data)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers)
    else:
        raise ValueError(f"Unknown method: {method}")

    if resp.status_code >= 400:
        print_warning(f"API error {resp.status_code}: {resp.text[:500]}")
        return None

    if resp.text:
        return resp.json()
    return {}


# ##################################################################
# api request
# makes a request to app store connect api


# ##################################################################
# wait for build processing
# waits for the build to finish processing in app store connect
def wait_for_build_processing(state: ProjectState, max_wait_minutes: int = 30) -> bool:
    print_info("Waiting for build to finish processing...")
    print_info("(This can take 10-30 minutes)")

    api_key_json = API_KEY_PATH.parent.parent / "api_key.json"

    # Poll every 2 minutes
    for i in range(max_wait_minutes // 2):
        # Check build status using fastlane
        ret_code, output = exec_cmd(
            [
                "fastlane",
                "run",
                "latest_testflight_build_number",
                "app_identifier:" + state.bundle_id,
                "api_key_path:" + str(api_key_json),
            ]
        )

        if ret_code == 0 and str(state.current_build) in output:
            print_success(f"Build {state.current_build} is ready")
            return True

        if i < (max_wait_minutes // 2) - 1:
            print_info(f"Build still processing... (checked {i + 1} times)")
            Event().wait(120)  # Wait 2 minutes

    print_warning("Build processing is taking longer than expected")
    print_info("You may need to submit manually from App Store Connect")
    return False


# ##################################################################
# wait for build processing
# waits for the build to finish processing in app store connect


# ##################################################################
# submit for review
# submits the app for app store review via app store connect api
def submit_for_review(project_path: Path, state: ProjectState, base_url: str = API_BASE_URL) -> bool:
    print_info("Submitting for App Store review via API...")

    try:
        token = get_api_token()
    except Exception as e:
        print_error(f"Failed to generate API token: {e}")
        return False

    def request(method: str, endpoint: str, data: dict | None = None) -> dict | None:
        return api_request(method, endpoint, token, data, base_url)

    # Get app ID (should be a UUID, not the numeric App Store ID)
    result = request("GET", f"apps?filter[bundleId]={state.bundle_id}")
    if not result or not result.get("data"):
        print_error("Could not find app in App Store Connect")
        return False
    app_id = result["data"][0]["id"]
    print_info(f"App ID from API: {app_id}")

    # Get the version in PREPARE_FOR_SUBMISSION state that matches our version number
    result = request(
        "GET",
        f"apps/{app_id}/appStoreVersions?filter[appStoreState]=PREPARE_FOR_SUBMISSION",
    )
    if not result or not result.get("data"):
        # Check if already in review
        result = request(
            "GET",
            f"apps/{app_id}/appStoreVersions?filter[appStoreState]=WAITING_FOR_REVIEW,IN_REVIEW",
        )
        if result and result.get("data"):
            print_info("App is already submitted for review")
            return True
        print_error("No version ready for submission")
        return False

    # Find the version that matches state.current_version
    target_version = state.current_version
    version_data = None
    for v in result["data"]:
        v_string = v.get("attributes", {}).get("versionString", "")
        if v_string == target_version:
            version_data = v
            break

    if not version_data:
        # Log what versions we found
        found_versions = [v.get("attributes", {}).get("versionString", "?") for v in result["data"]]
        print_error(f"Version {target_version} not found. Found versions: {found_versions}")
        print_info("You may need to create this version in App Store Connect first")
        return False

    version_id = version_data["id"]
    version_state = version_data.get("attributes", {}).get("appStoreState", "UNKNOWN")
    print_info(f"Found version ID: {version_id} (version {target_version}, state: {version_state})")

    # Check if there are any issues with the version
    result = request("GET", f"appStoreVersions/{version_id}?include=appStoreVersionSubmission,build")
    if result:
        attrs = result.get("data", {}).get("attributes", {})
        if attrs.get("appStoreState") != "PREPARE_FOR_SUBMISSION":
            print_warning(f"Version state is {attrs.get('appStoreState')}, may not be ready")

        # Check for build
        included = result.get("included", [])
        has_build = any(item.get("type") == "builds" for item in included)
        if not has_build:
            print_warning("No build attached to this version!")
            # Try to find and attach a build - try multiple queries
            build_id = None

            # First try: VALID builds
            print_info(f"Searching for builds for app {app_id}...")
            build_result = request(
                "GET",
                f"builds?filter[app]={app_id}&filter[processingState]=VALID&sort=-uploadedDate&limit=1",
            )
            if build_result and build_result.get("data"):
                build_id = build_result["data"][0]["id"]
                print_info(f"Found VALID build: {build_id}")
            else:
                print_info("No VALID builds found via filtered query")

            # Second try: Any builds (might be in a different state)
            if not build_id:
                print_info("Trying unfiltered build query...")
                build_result = request("GET", f"builds?filter[app]={app_id}&sort=-uploadedDate&limit=5")
                if build_result:
                    builds = build_result.get("data", [])
                    print_info(f"Found {len(builds)} builds total")
                    if builds:
                        for b in builds:
                            state = b["attributes"].get("processingState", "UNKNOWN")
                            version = b["attributes"].get("version", "?")
                            print_info(f"  Build {version}: {state} (id: {b['id']})")
                            # Use the first build that's ready for distribution
                            if state in ["VALID", "READY_FOR_DISTRIBUTION"] and not build_id:
                                build_id = b["id"]
                    else:
                        print_warning("API returned empty builds list")
                else:
                    print_warning("Builds API query failed")

            if build_id:
                print_info(f"Attaching build {build_id} to version...")
                attach_data = {"data": {"type": "builds", "id": build_id}}
                attach_result = request(
                    "PATCH",
                    f"appStoreVersions/{version_id}/relationships/build",
                    attach_data,
                )
                if attach_result is not None:
                    print_success("Build attached to version")
                else:
                    print_warning("Failed to attach build")

    # Step 1: Create review submission
    review_submission_data = {
        "data": {
            "type": "reviewSubmissions",
            "attributes": {"platform": "IOS"},
            "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
        }
    }
    result = request("POST", "reviewSubmissions", review_submission_data)
    if not result:
        print_warning("Failed to create review submission")
        print_error("Submission failed via API")
        print_info("Submit manually from: https://appstoreconnect.apple.com")
        return False

    submission_id = result["data"]["id"]
    print_info(f"Created review submission: {submission_id}")

    # Step 2: Add the app store version to the submission as a review item
    review_item_data = {
        "data": {
            "type": "reviewSubmissionItems",
            "relationships": {
                "reviewSubmission": {"data": {"type": "reviewSubmissions", "id": submission_id}},
                "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}},
            },
        }
    }
    result = request("POST", "reviewSubmissionItems", review_item_data)
    if not result:
        print_warning("Failed to add version to review submission")
        # Try to delete the incomplete submission
        request("DELETE", f"reviewSubmissions/{submission_id}")
        print_error("Submission failed via API")
        print_info("Submit manually from: https://appstoreconnect.apple.com")
        return False

    print_info("Added version to review submission")

    # Step 3: Confirm/submit the review
    confirm_data = {
        "data": {
            "type": "reviewSubmissions",
            "id": submission_id,
            "attributes": {"submitted": True},
        }
    }
    result = request("PATCH", f"reviewSubmissions/{submission_id}", confirm_data)
    if result:
        print_success("App submitted for review!")
        return True

    print_warning("Failed to confirm submission")
    print_error("Submission failed via API")
    print_info("Submit manually from: https://appstoreconnect.apple.com")
    return False


# ##################################################################
# submit for review
# submits the app for app store review via app store connect api


# ##################################################################
# tag release
# create a git tag for this release
def tag_release(project_path: Path, state: ProjectState) -> bool:
    tag_name = f"v{state.current_version}"
    print_info(f"Creating release tag: {tag_name}")

    ret_code, output = exec_cmd(
        [
            "git",
            "tag",
            "-a",
            tag_name,
            "-m",
            f"Release {state.current_version} (build {state.current_build})",
        ],
        cwd=project_path,
    )

    if ret_code != 0:
        # tag might already exist
        if "already exists" in output.lower():
            print_info(f"Tag {tag_name} already exists")
            return True
        print_warning(f"Failed to create tag: {output}")
        return False

    print_success(f"Created release tag: {tag_name}")

    # push the tag
    ret_code, output = exec_cmd(
        ["git", "push", "origin", tag_name],
        cwd=project_path,
    )

    if ret_code != 0:
        print_warning(f"Failed to push tag (you can push manually): git push origin {tag_name}")
    else:
        print_success("Tag pushed to remote")

    return True


# ##################################################################
# tag release


# ##################################################################
# run
# runs submit step and submits the app for app store review
def run(project_path: Path, state: ProjectState) -> bool:
    # Wait for build to be processed
    if not wait_for_build_processing(state, max_wait_minutes=30):
        print_error("Build not ready for submission")
        print_info("Check App Store Connect and retry when build is processed")
        return False

    # Submit for review
    if not submit_for_review(project_path, state):
        print_error("Submission failed")
        print_info("Fix the issue above and retry, or submit manually from:")
        print_info("https://appstoreconnect.apple.com")
        return False

    print_success("App is now waiting for Apple review")
    print_info("You'll receive an email when the review is complete")

    # Tag this release in git
    tag_release(project_path, state)

    return True


# ##################################################################
# run
# runs submit step and submits the app for app store review


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python submit.py <project_path>")
        sys.exit(1)

    project_path = Path(sys.argv[1]).resolve()
    state = load_state(project_path)

    if not state.bundle_id:
        print_error("No bundle_id in state - run structure step first")
        sys.exit(1)

    success = run(project_path, state)
    if success:
        save_state(project_path, state)
        print_success("Submit step completed successfully!")
    else:
        print_error("Submit step failed!")
        sys.exit(1)
