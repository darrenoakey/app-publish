# app store module - creates the app in app store connect
#
# handles:
# - checking if app already exists via app store connect api
# - opening the website and providing exact instructions if app doesn't exist
# - getting the app id for subsequent operations

import time
import webbrowser
import subprocess
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState, load_state, save_state
from config import API_KEY_ID, API_ISSUER_ID, API_KEY_PATH
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    file_exists,
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

    key_path = Path(API_KEY_PATH).expanduser()
    if not key_path.exists():
        return None

    private_key = key_path.read_text()

    # Token expires in 20 minutes
    expiration = int(time.time()) + 20 * 60

    payload = {
        "iss": API_ISSUER_ID,
        "iat": int(time.time()),
        "exp": expiration,
        "aud": "appstoreconnect-v1",
    }

    headers = {"alg": "ES256", "kid": API_KEY_ID, "typ": "JWT"}

    token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
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
# open app store connect and show instructions
# opens app store connect website and prints exact instructions for creating new app
def open_app_store_connect_and_show_instructions(state: ProjectState):
    # Prepare all the values - fail if not set
    if not state.app_name:
        print_error("app_name not set in state")
        return
    if not state.bundle_id:
        print_error("bundle_id not set in state")
        return
    # Open the website
    url = "https://appstoreconnect.apple.com/apps"
    print_info(f"Opening: {url}")

    try:
        # Use 'open' command on macOS
        subprocess.run(["open", url], check=True)
    except Exception:
        try:
            webbrowser.open(url)
        except Exception:
            print_info(f"Please open manually: {url}")

    for line in creation_instructions(state):
        print(line)


# ##################################################################
# open app store connect and show instructions
# opens app store connect website and prints exact instructions for creating new app


# ##################################################################
# check app exists
# checks if app exists in app store connect and gets its id
def check_app_exists(project_path: Path, state: ProjectState, base_url: str = BASE_URL) -> bool:
    print_info(f"Checking App Store Connect for: {state.bundle_id}")

    # Try direct API first
    exists, app_id = check_app_exists_api(state.bundle_id, base_url)

    if exists is True:
        state.app_store_id = app_id
        print_success("App found in App Store Connect")
        print_info(f"App Store ID: {app_id}")
        return True
    elif exists is False:
        # App definitely doesn't exist - show instructions
        print_warning("App not found in App Store Connect")
        open_app_store_connect_and_show_instructions(state)
        return False
    else:
        # API check failed, try fastlane
        print_info("Trying fastlane...")
        return check_app_exists_fastlane(project_path, state)


# ##################################################################
# check app exists
# checks if app exists in app store connect and gets its id


# ##################################################################
# check app exists fastlane
# check if app exists using fastlane
def check_app_exists_fastlane(project_path: Path, state: ProjectState) -> bool:
    # Ensure the lane exists
    if not ensure_create_app_lane(project_path, state):
        return False

    ret_code, output = exec_cmd(["fastlane", "ios", "create_app"], cwd=project_path, timeout=120)

    app_exists, app_id = parse_fastlane_result(output)
    if app_id:
        state.app_store_id = app_id
        print_info(f"App Store ID: {app_id}")

    if app_exists:
        print_success("App found in App Store Connect")
        return True

    # App doesn't exist - show instructions
    print_warning("App not found in App Store Connect")
    open_app_store_connect_and_show_instructions(state)
    return False


# ##################################################################
# check app exists fastlane
# check if app exists using fastlane


# ##################################################################
# ensure create app lane
# ensures the create_app lane exists in fastfile
def ensure_create_app_lane(project_path: Path, state: ProjectState) -> bool:
    fastfile_path = project_path / "fastlane" / "Fastfile"

    if not file_exists(fastfile_path):
        print_error("Fastfile not found - run structure step first")
        return False

    content = fastfile_path.read_text()

    # Check if lane already exists
    if "lane :create_app" in content:
        return True

    lane_content = f'''

  lane :create_app do
    require 'spaceship'

    bundle_id = "{state.bundle_id}"
    app_name = "{state.app_name}"

    # Authenticate with App Store Connect API
    token = Spaceship::ConnectAPI::Token.create(
      key_id: "{API_KEY_ID}",
      issuer_id: "{API_ISSUER_ID}",
      filepath: File.expand_path("{API_KEY_PATH}"),
      in_house: false
    )
    Spaceship::ConnectAPI.token = token

    # Check if app already exists
    existing_app = Spaceship::ConnectAPI::App.find(bundle_id)
    if existing_app
      UI.success("App exists in App Store Connect: #{{existing_app.name}}")
      UI.success("App Store ID: #{{existing_app.id}}")
      puts "APP_STORE_ID=#{{existing_app.id}}"
      puts "APP_EXISTS=true"
    else
      puts "APP_EXISTS=false"
      UI.user_error!("App must be created in App Store Connect first")
    end
  end
'''

    # Insert before the final 'end'
    if content.rstrip().endswith("end"):
        content = content.rstrip()[:-3] + lane_content + "\nend\n"
    else:
        content = content + lane_content

    fastfile_path.write_text(content)
    print_info("Added create_app lane to Fastfile")
    return True


# ##################################################################
# ensure create app lane
# ensures the create_app lane exists in fastfile


# ##################################################################
# run
# runs app store creation step
# checks if app exists in app store connect, if not opens website with instructions
def run(project_path: Path, state: ProjectState, base_url: str = BASE_URL) -> bool:
    if not state.bundle_id:
        print_error("No bundle ID - run identity step first")
        return False

    if not state.app_name:
        print_error("No app name - run identity step first")
        return False

    # Check if app exists (will open website and show instructions if not)
    if not check_app_exists(project_path, state, base_url):
        return False

    return True


# ##################################################################
# run
# runs app store creation step
# checks if app exists in app store connect, if not opens website with instructions


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
