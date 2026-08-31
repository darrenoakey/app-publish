# app store module - creates the app in app store connect
#
# JWT GET finds an existing app. Creating a new app is iris POST
# /v1/apps with the seeded Chrome Apple ID session — the public
# JWT API returns 403 FORBIDDEN_ERROR apps does not allow CREATE.
from pathlib import Path

import sys
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState, load_state, save_state
from config import API_KEY_ID, API_ISSUER_ID, API_PRIVATE_KEY
from notices import PostgresNoticeStore
from session import IrisClient, SessionExpired, default_iris
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
)

try:
    import jwt
    import requests

    HAS_JWT = True
except ImportError:
    HAS_JWT = False


BASE_URL = "https://api.appstoreconnect.apple.com/v1"


def creation_instructions(state: ProjectState) -> list[str]:
    sku = state.bundle_id.replace(".", "_")
    return [
        "",
        "=" * 70,
        "CREATE NEW APP IN APP STORE CONNECT",
        "=" * 70,
        "",
        "1. Click the '+' button (top left, next to 'Apps')",
        "2. Select 'New App'",
        "",
        "3. Fill in the form with these EXACT values:",
        "",
        "-" * 70,
        "   Platforms:          [x] iOS",
        "-" * 70,
        f"   Name:               {state.app_name}",
        "-" * 70,
        "   Primary Language:   English (U.S.)",
        "-" * 70,
        f"   Bundle ID:          {state.bundle_id}",
        "                       (Select from dropdown - must match exactly)",
        "-" * 70,
        f"   SKU:                {sku}",
        "-" * 70,
        "   User Access:        Full Access (or as needed)",
        "-" * 70,
        "",
        "4. Click 'Create'",
        "",
        "=" * 70,
        "",
        "After creating the app, run this step again to continue.",
        "",
    ]


# ##################################################################
# create jwt token
# creates jwt token for app store connect api
def create_jwt_token() -> str:
    if not HAS_JWT:
        return None

    if not API_PRIVATE_KEY:
        return None

    # Token expires in 20 minutes
    expiration = int(time.time()) + 20 * 60

    payload = {
        "iss": API_ISSUER_ID,
        "iat": int(time.time()),
        "exp": expiration,
        "aud": "appstoreconnect-v1",
    }

    headers = {"alg": "ES256", "kid": API_KEY_ID, "typ": "JWT"}

    token = jwt.encode(payload, API_PRIVATE_KEY, algorithm="ES256", headers=headers)
    return token


# ##################################################################
# create jwt token
# creates jwt token for app store connect api


# ##################################################################
# get headers
# gets authorization headers for api requests
def get_headers():
    token = create_jwt_token()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def parse_apps_response(data: dict) -> tuple[bool, str | None]:
    apps = data.get("data", [])
    if not apps:
        return False, None
    return True, apps[0]["id"]


def parse_fastlane_result(output: str) -> tuple[bool, str | None]:
    exists = False
    app_id = None
    for line in output.splitlines():
        if "APP_EXISTS=true" in line:
            exists = True
        if "APP_STORE_ID=" in line:
            app_id = line.split("=", 1)[1].strip()
    return exists, app_id


# ##################################################################
# get headers
# gets authorization headers for api requests


# ##################################################################
# check app exists api
# checks if app exists in app store connect via direct api call
# returns tuple of (exists: bool, app_id: str | None)
def check_app_exists_api(bundle_id: str, base_url: str = BASE_URL) -> tuple[bool, str | None]:
    if not HAS_JWT:
        print_warning("PyJWT or requests not installed - using fastlane instead")
        return None, None

    headers = get_headers()
    if not headers:
        print_warning("Could not create API token")
        return None, None

    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/apps",
            headers=headers,
            params={"filter[bundleId]": bundle_id},
            timeout=30,
        )

        if response.status_code != 200:
            print_warning(f"API request failed: {response.status_code}")
            return None, None

        return parse_apps_response(response.json())

    except Exception as e:
        print_warning(f"API error: {e}")
        return None, None


# ##################################################################
# check app exists api
# checks if app exists in app store connect via direct api call
# returns tuple of (exists: bool, app_id: str | None)


# ##################################################################
# create app body
# Spaceship ConnectAPI post_app payload for a single iOS app
def create_app_body(name: str, bundle_id: str, sku: str, version_string: str) -> dict:
    locale = "en-US"
    return {
        "data": {
            "type": "apps",
            "attributes": {
                "sku": sku,
                "primaryLocale": locale,
                "bundleId": bundle_id,
            },
            "relationships": {
                "appStoreVersions": {"data": [{"type": "appStoreVersions", "id": "${store-version-IOS}"}]},
                "appInfos": {"data": [{"type": "appInfos", "id": "${new-appInfo-id}"}]},
            },
        },
        "included": [
            {
                "type": "appInfos",
                "id": "${new-appInfo-id}",
                "relationships": {
                    "appInfoLocalizations": {
                        "data": [
                            {
                                "type": "appInfoLocalizations",
                                "id": "${new-appInfoLocalization-id}",
                            }
                        ]
                    }
                },
            },
            {
                "type": "appInfoLocalizations",
                "id": "${new-appInfoLocalization-id}",
                "attributes": {"locale": locale, "name": name},
            },
            {
                "type": "appStoreVersions",
                "id": "${store-version-IOS}",
                "attributes": {"platform": "IOS", "versionString": version_string},
                "relationships": {
                    "appStoreVersionLocalizations": {
                        "data": [
                            {
                                "type": "appStoreVersionLocalizations",
                                "id": "${new-IOSVersionLocalization-id}",
                            }
                        ]
                    }
                },
            },
            {
                "type": "appStoreVersionLocalizations",
                "id": "${new-IOSVersionLocalization-id}",
                "attributes": {"locale": locale},
            },
        ],
    }


# ##################################################################
# create app
# iris POST /v1/apps using the Chrome Apple ID session
def create_app(state: ProjectState, iris: IrisClient) -> str:
    sku = state.bundle_id.replace(".", "_")
    document = iris.request(
        "POST",
        "v1/apps",
        json_body=create_app_body(
            name=state.app_name,
            bundle_id=state.bundle_id,
            sku=sku,
            version_string=state.current_version or "1.0",
        ),
    )
    return str(document["data"]["id"])


# ##################################################################
# session expired notice
# tell Beezle the Mac mini Chrome profile is not logged into App Store Connect
def session_expired_notice(state: ProjectState, notices=None) -> None:
    store = notices or PostgresNoticeStore()
    store.notify(
        "app-publish",
        f"session-create:{state.bundle_id}",
        (
            "App Store Connect Chrome session expired. Cannot create "
            f"{state.app_name} ({state.bundle_id}). Log into "
            "appstoreconnect.apple.com in the Mac mini Chrome profile used by web-driver."
        ),
    )


# ##################################################################
# check app exists
# JWT lookup only — creation is iris POST in run()
def check_app_exists(project_path: Path, state: ProjectState, base_url: str = BASE_URL) -> bool:
    del project_path
    print_info(f"Checking App Store Connect for: {state.bundle_id}")
    exists, app_id = check_app_exists_api(state.bundle_id, base_url)
    if exists is True:
        state.app_store_id = app_id
        print_success("App found in App Store Connect")
        print_info(f"App Store ID: {app_id}")
        return True
    if exists is False:
        print_warning("App not found in App Store Connect")
        return False
    print_error("App Store Connect API check failed")
    return False


# ##################################################################
# run
# find the app via JWT or create it via iris session
def run(
    project_path: Path,
    state: ProjectState,
    base_url: str = BASE_URL,
    iris: IrisClient | None = None,
    notices=None,
) -> bool:
    if not state.bundle_id:
        print_error("No bundle ID - run identity step first")
        return False
    if not state.app_name:
        print_error("No app name - run identity step first")
        return False
    print_info(f"Checking App Store Connect for: {state.bundle_id}")
    exists, app_id = check_app_exists_api(state.bundle_id, base_url)
    if exists is True:
        state.app_store_id = app_id
        print_success("App found in App Store Connect")
        print_info(f"App Store ID: {app_id}")
        return True
    if exists is None:
        print_error("App Store Connect API check failed")
        return False
    print_info("App not found; creating via iris session")
    client = iris or default_iris()
    try:
        created = create_app(state, client)
    except SessionExpired as err:
        session_expired_notice(state, notices)
        print_error(str(err))
        return False
    state.app_store_id = created
    print_success("App created in App Store Connect")
    print_info(f"App Store ID: {created}")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python appstore.py <project_path>")
        sys.exit(1)

    project_path = Path(sys.argv[1]).resolve()
    state = load_state(project_path)

    if not state.bundle_id:
        print_error("No bundle_id in state - run structure step first")
        sys.exit(1)

    success = run(project_path, state)
    if success:
        save_state(project_path, state)
        print_success("App Store creation step completed successfully!")
    else:
        print_error("App Store creation step failed!")
        sys.exit(1)
