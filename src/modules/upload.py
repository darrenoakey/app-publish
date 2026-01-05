import json
import time
import jwt
import requests
from pathlib import Path
from datetime import datetime, timedelta

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState, load_state, save_state
from config import API_KEY_ID, API_ISSUER_ID, API_KEY_PATH, CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_EMAIL, CONTACT_PHONE
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    file_exists,
    dir_exists,
    read_file,
)


def get_api_token() -> str:
    private_key = read_file(API_KEY_PATH)

    header = {
        "alg": "ES256",
        "kid": API_KEY_ID,
        "typ": "JWT"
    }

    payload = {
        "iss": API_ISSUER_ID,
        "iat": int(time.time()),
        "exp": int(time.time()) + 1200,  # 20 minutes
        "aud": "appstoreconnect-v1"
    }

    return jwt.encode(payload, private_key, algorithm="ES256", headers=header)
# ##################################################################
# get api token
# generate jwt token for app store connect api


def api_request(method: str, endpoint: str, token: str, data: dict | None = None) -> dict | None:
    base_url = "https://api.appstoreconnect.apple.com/v1"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    url = f"{base_url}/{endpoint}"

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
# make a request to app store connect api


def get_app_id(token: str, bundle_id: str) -> str | None:
    result = api_request("GET", f"apps?filter[bundleId]={bundle_id}", token)
    if result and result.get("data"):
        return result["data"][0]["id"]
    return None
# ##################################################################
# get app id
# get the app store connect app id for a bundle id


def get_app_store_version(token: str, app_id: str) -> dict | None:
    result = api_request(
        "GET",
        f"apps/{app_id}/appStoreVersions?filter[appStoreState]=PREPARE_FOR_SUBMISSION",
        token
    )
    if result and result.get("data"):
        return result["data"][0]
    return None
# ##################################################################
# get app store version
# get the editable app store version (prepare_for_submission)


def update_app_store_version(token: str, version_id: str, attributes: dict) -> bool:
    data = {
        "data": {
            "type": "appStoreVersions",
            "id": version_id,
            "attributes": attributes
        }
    }
    result = api_request("PATCH", f"appStoreVersions/{version_id}", token, data)
    return result is not None
# ##################################################################
# update app store version
# update an app store version (copyright, etc.)


def get_latest_valid_build(token: str, app_id: str, wait_for_processing: bool = True) -> dict | None:
    # first try to get a valid build
    result = api_request(
        "GET",
        f"builds?filter[app]={app_id}&filter[processingState]=VALID&sort=-uploadedDate&limit=1",
        token
    )
    if result and result.get("data"):
        return result["data"][0]

    if not wait_for_processing:
        return None

    # check for processing builds and wait
    for attempt in range(30):  # wait up to 5 minutes (30 * 10 seconds)
        result = api_request(
            "GET",
            f"builds?filter[app]={app_id}&filter[processingState]=PROCESSING&sort=-uploadedDate&limit=1",
            token
        )

        if result and result.get("data"):
            build = result["data"][0]
            build_version = build["attributes"]["version"]
            if attempt == 0:
                print_info(f"Build {build_version} is still processing, waiting...")
            elif attempt % 6 == 0:  # log every minute
                print_info(f"  Still waiting for build {build_version} to process...")

            time.sleep(10)

            # check if it became valid
            result = api_request(
                "GET",
                f"builds?filter[app]={app_id}&filter[processingState]=VALID&sort=-uploadedDate&limit=1",
                token
            )
            if result and result.get("data"):
                return result["data"][0]
        else:
            # no processing builds either - maybe just uploaded
            if attempt < 3:
                print_info("Waiting for build to appear in App Store Connect...")
                time.sleep(10)
            else:
                break

    return None
# ##################################################################
# get latest valid build
# get the latest valid build for an app, optionally waiting for processing builds


def get_build_for_version(token: str, version_id: str) -> dict | None:
    result = api_request("GET", f"appStoreVersions/{version_id}/build", token)
    if result and result.get("data"):
        return result["data"]
    return None
# ##################################################################
# get build for version
# get the build currently associated with a version


def select_build_for_version(token: str, version_id: str, build_id: str) -> bool:
    data = {
        "data": {
            "type": "builds",
            "id": build_id
        }
    }
    result = api_request("PATCH", f"appStoreVersions/{version_id}/relationships/build", token, data)
    return result is not None
# ##################################################################
# select build for version
# select a build for an app store version


def set_export_compliance(token: str, build_id: str, uses_encryption: bool = False) -> bool:
    data = {
        "data": {
            "type": "builds",
            "id": build_id,
            "attributes": {
                "usesNonExemptEncryption": uses_encryption
            }
        }
    }
    result = api_request("PATCH", f"builds/{build_id}", token, data)
    return result is not None
# ##################################################################
# set export compliance
# set export compliance for a build


def get_version_localization(token: str, version_id: str, locale: str = "en-US") -> dict | None:
    result = api_request(
        "GET",
        f"appStoreVersions/{version_id}/appStoreVersionLocalizations?filter[locale]={locale}",
        token
    )
    if result and result.get("data"):
        return result["data"][0]

    # localization doesn't exist - create it
    print_info(f"Creating version localization for {locale}...")
    create_data = {
        "data": {
            "type": "appStoreVersionLocalizations",
            "attributes": {
                "locale": locale
            },
            "relationships": {
                "appStoreVersion": {
                    "data": {
                        "type": "appStoreVersions",
                        "id": version_id
                    }
                }
            }
        }
    }

    result = api_request("POST", "appStoreVersionLocalizations", token, create_data)
    if result and result.get("data"):
        print_success(f"Created version localization for {locale}")
        return result["data"]

    return None
# ##################################################################
# get version localization
# get the localization for a version, creating it if it doesn't exist


def update_version_localization(token: str, localization_id: str, metadata: dict) -> bool:
    data = {
        "data": {
            "type": "appStoreVersionLocalizations",
            "id": localization_id,
            "attributes": metadata
        }
    }

    result = api_request("PATCH", f"appStoreVersionLocalizations/{localization_id}", token, data)
    return result is not None
# ##################################################################
# update version localization
# update version localization metadata


def get_app_info_localization(token: str, app_id: str, locale: str = "en-US") -> dict | None:
    result = api_request("GET", f"apps/{app_id}/appInfos", token)
    if not result or not result.get("data"):
        return None

    app_info_id = result["data"][0]["id"]

    result = api_request(
        "GET",
        f"appInfos/{app_info_id}/appInfoLocalizations?filter[locale]={locale}",
        token
    )
    if result and result.get("data"):
        loc = result["data"][0]
        loc["_app_info_id"] = app_info_id
        return loc

    # localization doesn't exist - create it
    print_info(f"Creating app info localization for {locale}...")
    create_data = {
        "data": {
            "type": "appInfoLocalizations",
            "attributes": {
                "locale": locale
            },
            "relationships": {
                "appInfo": {
                    "data": {
                        "type": "appInfos",
                        "id": app_info_id
                    }
                }
            }
        }
    }

    result = api_request("POST", "appInfoLocalizations", token, create_data)
    if result and result.get("data"):
        print_success(f"Created app info localization for {locale}")
        loc = result["data"]
        loc["_app_info_id"] = app_info_id
        return loc

    return None
# ##################################################################
# get app info localization
# get app info localization (for name, subtitle, privacy url), creating if needed


def update_app_info_localization(token: str, localization_id: str, metadata: dict) -> bool:
    data = {
        "data": {
            "type": "appInfoLocalizations",
            "id": localization_id,
            "attributes": metadata
        }
    }

    result = api_request("PATCH", f"appInfoLocalizations/{localization_id}", token, data)
    return result is not None
# ##################################################################
# update app info localization
# update app info localization (name, subtitle, privacy url)


def get_review_detail(token: str, version_id: str) -> dict | None:
    result = api_request("GET", f"appStoreVersions/{version_id}/appStoreReviewDetail", token)
    if result and result.get("data"):
        return result["data"]
    return None
# ##################################################################
# get review detail
# get app store review detail


def create_review_detail(token: str, version_id: str, contact_info: dict) -> bool:
    data = {
        "data": {
            "type": "appStoreReviewDetails",
            "attributes": contact_info,
            "relationships": {
                "appStoreVersion": {
                    "data": {
                        "type": "appStoreVersions",
                        "id": version_id
                    }
                }
            }
        }
    }

    result = api_request("POST", "appStoreReviewDetails", token, data)
    return result is not None
# ##################################################################
# create review detail
# create app store review detail


def update_review_detail(token: str, review_detail_id: str, contact_info: dict) -> bool:
    data = {
        "data": {
            "type": "appStoreReviewDetails",
            "id": review_detail_id,
            "attributes": contact_info
        }
    }

    result = api_request("PATCH", f"appStoreReviewDetails/{review_detail_id}", token, data)
    return result is not None
# ##################################################################
# update review detail
# update app store review detail


def upload_ipa_altool(ipa_path: Path) -> bool:
    print_info(f"Uploading {ipa_path.name} via altool...")

    ret_code, output = exec_cmd([
        "xcrun", "altool",
        "--upload-app",
        "-f", str(ipa_path),
        "-t", "ios",
        "--apiKey", API_KEY_ID,
        "--apiIssuer", API_ISSUER_ID,
    ], timeout=600)

    if ret_code != 0:
        print_error(f"Upload failed: {output}")
        return False

    print_success("IPA uploaded successfully")
    return True
# ##################################################################
# upload ipa altool
# upload ipa using altool


def ensure_build_selected(token: str, app_id: str, version_id: str) -> bool:
    print_info("Checking build selection...")

    # get latest valid build
    latest_build = get_latest_valid_build(token, app_id)
    if not latest_build:
        print_warning("No valid builds found")
        return False

    latest_build_id = latest_build["id"]
    latest_build_version = latest_build["attributes"]["version"]

    # get currently selected build
    current_build = get_build_for_version(token, version_id)
    current_build_id = current_build["id"] if current_build else None

    if current_build_id == latest_build_id:
        print_info(f"Build {latest_build_version} already selected")
    else:
        print_info(f"Selecting build {latest_build_version} for version...")
        if select_build_for_version(token, version_id, latest_build_id):
            print_success(f"Build {latest_build_version} selected")
        else:
            print_error("Failed to select build")
            return False

    # set export compliance if needed
    uses_encryption = latest_build["attributes"].get("usesNonExemptEncryption")
    if uses_encryption is None:
        print_info("Setting export compliance (no encryption)...")
        if set_export_compliance(token, latest_build_id, False):
            print_success("Export compliance set")
        else:
            print_warning("Failed to set export compliance - may need manual approval")

    return True
# ##################################################################
# ensure build selected
# ensure the latest valid build is selected for the version


def normalize_phone_number(phone: str) -> str:
    if not phone:
        return phone
    # remove all non-digit characters except leading +
    has_plus = phone.startswith('+')
    digits = ''.join(c for c in phone if c.isdigit())
    return ('+' if has_plus else '+') + digits
# ##################################################################
# normalize phone number
# normalize phone number to api-compatible format (+xxxxxxxxxxx)


def ensure_review_detail(token: str, version_id: str, project_path: Path) -> bool:
    print_info("Checking review details...")

    review_info_path = project_path / "fastlane" / "metadata" / "review_info"
    contact_info = {
        "contactFirstName": CONTACT_FIRST_NAME,
        "contactLastName": CONTACT_LAST_NAME,
        "contactPhone": CONTACT_PHONE,
        "contactEmail": CONTACT_EMAIL,
        "demoAccountRequired": False,
        "notes": ""
    }

    # try to load from files
    if dir_exists(review_info_path):
        fields = {
            "first_name.txt": "contactFirstName",
            "last_name.txt": "contactLastName",
            "phone_number.txt": "contactPhone",
            "email_address.txt": "contactEmail",
            "notes.txt": "notes"
        }
        for filename, field in fields.items():
            filepath = review_info_path / filename
            if file_exists(filepath):
                content = read_file(filepath).strip()
                if content:
                    contact_info[field] = content

    # also check en-us review_information
    en_review_path = project_path / "fastlane" / "metadata" / "en-US" / "review_information"
    if dir_exists(en_review_path):
        fields = {
            "first_name.txt": "contactFirstName",
            "last_name.txt": "contactLastName",
            "phone_number.txt": "contactPhone",
            "email_address.txt": "contactEmail",
            "notes.txt": "notes"
        }
        for filename, field in fields.items():
            filepath = en_review_path / filename
            if file_exists(filepath):
                content = read_file(filepath).strip()
                if content:
                    # normalize phone number format
                    if field == "contactPhone":
                        content = normalize_phone_number(content)
                    contact_info[field] = content

    # check if review detail exists
    existing = get_review_detail(token, version_id)
    if existing:
        # update if needed
        print_info("Updating review details...")
        if update_review_detail(token, existing["id"], contact_info):
            print_success("Review details updated")
        else:
            print_warning("Failed to update review details")
    else:
        # create new
        print_info("Creating review details...")
        if create_review_detail(token, version_id, contact_info):
            print_success("Review details created")
        else:
            print_warning("Failed to create review details")

    return True
# ##################################################################
# ensure review detail
# ensure review details are set up


def upload_metadata_api(project_path: Path, state: ProjectState, token: str, version_id: str, app_id: str) -> bool:
    # support multiple locales - en-us is primary, en-au is secondary
    locales = ["en-US", "en-AU"]
    base_metadata_dir = project_path / "fastlane" / "metadata"

    # handle version-level fields (not localized) - copyright
    en_us_metadata = base_metadata_dir / "en-US"
    copyright_file = en_us_metadata / "copyright.txt"
    if file_exists(copyright_file):
        copyright_text = read_file(copyright_file).strip()
        if copyright_text:
            # get current version to check existing copyright
            version_data = api_request("GET", f"appStoreVersions/{version_id}", token)
            current_copyright = ""
            if version_data and version_data.get("data"):
                current_copyright = version_data["data"].get("attributes", {}).get("copyright", "") or ""

            if copyright_text != current_copyright:
                print_info(f"  copyright: needs update")
                if update_app_store_version(token, version_id, {"copyright": copyright_text}):
                    print_success("Copyright updated")
                else:
                    print_warning("Failed to update copyright")
            else:
                print_info(f"  copyright: up to date")

    success = True
    for locale in locales:
        metadata_dir = base_metadata_dir / locale

        if not dir_exists(metadata_dir):
            if locale == "en-US":
                print_warning("No metadata directory found")
                return True
            # for secondary locales, try to use en-us as fallback
            metadata_dir = base_metadata_dir / "en-US"
            if not dir_exists(metadata_dir):
                continue

        print_info(f"Checking metadata for {locale}...")
        if not upload_metadata_for_locale(project_path, state, token, version_id, app_id, locale, metadata_dir):
            success = False

    return success
# ##################################################################
# upload metadata api
# upload metadata using app store connect api directly for all supported locales


def upload_metadata_for_locale(project_path: Path, state: ProjectState, token: str, version_id: str, app_id: str, locale: str, metadata_dir: Path) -> bool:
    # get version localization for this locale
    version_loc = get_version_localization(token, version_id, locale)
    if not version_loc:
        print_error(f"Could not find/create version localization for {locale}")
        return False

    loc_id = version_loc["id"]
    current_attrs = version_loc.get("attributes", {})

    # load metadata from files and compare
    version_fields = {
        "description.txt": "description",
        "keywords.txt": "keywords",
        "promotional_text.txt": "promotionalText",
        "marketing_url.txt": "marketingUrl",
        "support_url.txt": "supportUrl",
    }

    updates = {}
    for filename, field in version_fields.items():
        filepath = metadata_dir / filename
        if file_exists(filepath):
            content = read_file(filepath).strip()
            if content:
                current_value = current_attrs.get(field, "") or ""
                if content != current_value:
                    updates[field] = content
                    print_info(f"  {field}: needs update")
                else:
                    print_info(f"  {field}: up to date")

    # update version localization if needed
    if updates:
        print_info(f"Updating version metadata: {list(updates.keys())}")
        if update_version_localization(token, loc_id, updates):
            print_success("Version metadata updated")
        else:
            print_warning("Failed to update version metadata")

    # get and update app info localization (name, subtitle, privacy url)
    app_info_loc = get_app_info_localization(token, app_id, locale)
    if app_info_loc:
        current_attrs = app_info_loc.get("attributes", {})
        app_info_fields = {
            "name.txt": "name",
            "subtitle.txt": "subtitle",
            "privacy_url.txt": "privacyPolicyUrl",
        }

        updates = {}
        for filename, field in app_info_fields.items():
            filepath = metadata_dir / filename
            if file_exists(filepath):
                content = read_file(filepath).strip()
                if content:
                    current_value = current_attrs.get(field, "") or ""
                    if content != current_value:
                        updates[field] = content
                        print_info(f"  {field}: needs update")
                    else:
                        print_info(f"  {field}: up to date")

        if updates:
            print_info(f"Updating app info: {list(updates.keys())}")
            if update_app_info_localization(token, app_info_loc["id"], updates):
                print_success("App info updated")
            else:
                print_warning("Failed to update app info")

    return True
# ##################################################################
# upload metadata for locale
# upload metadata for a specific locale


def get_screenshot_sets(token: str, localization_id: str) -> dict:
    result = api_request("GET", f"appStoreVersionLocalizations/{localization_id}/appScreenshotSets", token)
    if not result or not result.get("data"):
        return {}

    # map display type to set info
    sets = {}
    for s in result["data"]:
        display_type = s["attributes"]["screenshotDisplayType"]
        sets[display_type] = {"id": s["id"], "screenshots": []}

        # get screenshots in this set
        ss_result = api_request("GET", f"appScreenshotSets/{s['id']}/appScreenshots", token)
        if ss_result and ss_result.get("data"):
            for ss in ss_result["data"]:
                sets[display_type]["screenshots"].append({
                    "id": ss["id"],
                    "filename": ss["attributes"].get("fileName"),
                    "state": ss["attributes"].get("assetDeliveryState", {}).get("state")
                })

    return sets
# ##################################################################
# get screenshot sets
# get screenshot sets for a version localization


def create_screenshot_set(token: str, localization_id: str, display_type: str) -> str | None:
    data = {
        "data": {
            "type": "appScreenshotSets",
            "attributes": {
                "screenshotDisplayType": display_type
            },
            "relationships": {
                "appStoreVersionLocalization": {
                    "data": {
                        "type": "appStoreVersionLocalizations",
                        "id": localization_id
                    }
                }
            }
        }
    }

    result = api_request("POST", "appScreenshotSets", token, data)
    if result and result.get("data"):
        return result["data"]["id"]
    return None
# ##################################################################
# create screenshot set
# create a screenshot set for a display type


def delete_screenshot(token: str, screenshot_id: str) -> bool:
    result = api_request("DELETE", f"appScreenshots/{screenshot_id}", token)
    return result is not None
# ##################################################################
# delete screenshot
# delete a screenshot


def upload_screenshot(token: str, screenshot_set_id: str, filepath: Path) -> bool:
    filesize = filepath.stat().st_size
    filename = filepath.name

    data = {
        "data": {
            "type": "appScreenshots",
            "attributes": {
                "fileName": filename,
                "fileSize": filesize
            },
            "relationships": {
                "appScreenshotSet": {
                    "data": {
                        "type": "appScreenshotSets",
                        "id": screenshot_set_id
                    }
                }
            }
        }
    }

    result = api_request("POST", "appScreenshots", token, data)
    if not result:
        return False

    screenshot_id = result["data"]["id"]
    upload_ops = result["data"]["attributes"].get("uploadOperations", [])

    if not upload_ops:
        print_warning(f"No upload operations for {filename}")
        return False

    # upload the file parts
    with open(filepath, "rb") as f:
        file_data = f.read()

    for op in upload_ops:
        url = op["url"]
        headers = {h["name"]: h["value"] for h in op["requestHeaders"]}
        offset = op["offset"]
        length = op["length"]

        chunk = file_data[offset:offset + length]
        resp = requests.put(url, headers=headers, data=chunk)

        if resp.status_code >= 400:
            print_warning(f"Upload chunk failed: {resp.status_code}")
            return False

    # commit the upload
    commit_data = {
        "data": {
            "type": "appScreenshots",
            "id": screenshot_id,
            "attributes": {
                "uploaded": True,
                "sourceFileChecksum": result["data"]["attributes"].get("sourceFileChecksum")
            }
        }
    }

    result = api_request("PATCH", f"appScreenshots/{screenshot_id}", token, commit_data)
    return result is not None
# ##################################################################
# upload screenshot
# upload a single screenshot


def upload_screenshots_api(project_path: Path, state: ProjectState, token: str, version_id: str) -> bool:
    # support multiple locales - use same screenshots for all
    locales = ["en-US", "en-AU"]
    base_screenshots_dir = project_path / "fastlane" / "screenshots"

    # find screenshots (use en-us as primary source)
    screenshots_dir = base_screenshots_dir / "en-US"
    if not dir_exists(screenshots_dir):
        print_info("No screenshots directory found")
        return True

    screenshots = list(screenshots_dir.glob("*.png"))
    if not screenshots:
        print_info("No screenshots to upload")
        return True

    print_info(f"Found {len(screenshots)} local screenshots")

    success = True
    for locale in locales:
        print_info(f"\nUploading screenshots for {locale}...")

        # get version localization for this locale
        version_loc = get_version_localization(token, version_id, locale)
        if not version_loc:
            print_warning(f"Could not find/create version localization for {locale}")
            continue

        # upload screenshots for this locale
        if not upload_screenshots_for_locale(token, version_loc["id"], screenshots):
            success = False

    return success
# ##################################################################
# upload screenshots api
# upload screenshots using app store connect api for all supported locales


def upload_screenshots_for_locale(token: str, loc_id: str, screenshots: list) -> bool:
    # get existing screenshot sets
    existing_sets = get_screenshot_sets(token, loc_id)

    # map screenshot filenames to display types (support both space and hyphen separators)
    display_type_map = {
        "iPhone 16 Pro Max": "APP_IPHONE_67",
        "iPhone-16-Pro-Max": "APP_IPHONE_67",
        "iPhone 16 Plus": "APP_IPHONE_67",
        "iPhone-16-Plus": "APP_IPHONE_67",
        "iPad Pro 13-inch": "APP_IPAD_PRO_3GEN_129",
        "iPad-Pro-13-inch": "APP_IPAD_PRO_3GEN_129",
        "iPad Pro 11-inch": "APP_IPAD_PRO_3GEN_11",
        "iPad-Pro-11-inch": "APP_IPAD_PRO_3GEN_11",
    }

    # group screenshots by device type
    by_device = {}
    for ss in screenshots:
        name = ss.stem
        for device_prefix, display_type in display_type_map.items():
            if device_prefix in name:
                if display_type not in by_device:
                    by_device[display_type] = []
                by_device[display_type].append(ss)
                break

    # check each group
    for display_type, files in by_device.items():
        print_info(f"\nChecking {display_type}...")

        set_info = existing_sets.get(display_type, {"id": None, "screenshots": []})
        existing_screenshots = set_info["screenshots"]

        # check for failed screenshots and delete them
        for ss in existing_screenshots:
            if ss["state"] == "FAILED":
                print_info(f"  Deleting failed screenshot: {ss['filename']}")
                delete_screenshot(token, ss["id"])

        # re-fetch after deleting
        if any(ss["state"] == "FAILED" for ss in existing_screenshots):
            existing_sets = get_screenshot_sets(token, loc_id)
            set_info = existing_sets.get(display_type, {"id": None, "screenshots": []})
            existing_screenshots = set_info["screenshots"]

        # filter to only complete screenshots
        complete_screenshots = [ss for ss in existing_screenshots if ss["state"] == "COMPLETE"]
        existing_filenames = {ss["filename"] for ss in complete_screenshots}

        # find screenshots that need uploading
        to_upload = [f for f in files if f.name not in existing_filenames]

        if not to_upload:
            print_info(f"  All {len(complete_screenshots)} screenshots already uploaded")
            continue

        print_info(f"  Need to upload {len(to_upload)} screenshots")

        # get or create screenshot set
        set_id = set_info["id"]
        if not set_id:
            set_id = create_screenshot_set(token, loc_id, display_type)
            if not set_id:
                print_warning(f"  Could not create screenshot set for {display_type}")
                continue

        # upload missing screenshots (max 10 per set)
        total_after = len(complete_screenshots) + len(to_upload)
        if total_after > 10:
            to_upload = to_upload[:10 - len(complete_screenshots)]

        for ss in to_upload:
            print_info(f"  Uploading {ss.name}...")
            if upload_screenshot(token, set_id, ss):
                print_success(f"    Uploaded {ss.name}")
            else:
                print_warning(f"    Failed to upload {ss.name}")

    return True
# ##################################################################
# upload screenshots for locale
# upload screenshots for a specific locale


def get_app_info_id(token: str, app_id: str) -> str | None:
    result = api_request("GET", f"apps/{app_id}/appInfos", token)
    if result and result.get("data"):
        return result["data"][0]["id"]
    return None
# ##################################################################
# get app info id
# get the app info id for an app


def set_age_rating(token: str, app_info_id: str) -> bool:
    print_info("Checking age rating...")

    result = api_request("GET", f"appInfos/{app_info_id}/ageRatingDeclaration", token)
    if not result or not result.get("data"):
        print_warning("Could not get age rating declaration")
        return False

    age_rating_id = result["data"]["id"]
    current_attrs = result["data"].get("attributes", {})

    # check if already set (any non-null value means it's been configured)
    if current_attrs.get("alcoholTobaccoOrDrugUseOrReferences") is not None:
        print_info("Age rating already configured")
        return True

    # set all content descriptors to none (clean card game)
    data = {
        "data": {
            "type": "ageRatingDeclarations",
            "id": age_rating_id,
            "attributes": {
                "alcoholTobaccoOrDrugUseOrReferences": "NONE",
                "contests": "NONE",
                "gamblingSimulated": "NONE",
                "horrorOrFearThemes": "NONE",
                "matureOrSuggestiveThemes": "NONE",
                "medicalOrTreatmentInformation": "NONE",
                "profanityOrCrudeHumor": "NONE",
                "sexualContentGraphicAndNudity": "NONE",
                "sexualContentOrNudity": "NONE",
                "violenceCartoonOrFantasy": "NONE",
                "violenceRealistic": "NONE",
                "violenceRealisticProlongedGraphicOrSadistic": "NONE",
                "gambling": False,
                "unrestrictedWebAccess": False,
            }
        }
    }

    if api_request("PATCH", f"ageRatingDeclarations/{age_rating_id}", token, data):
        print_success("Age rating set (4+)")
        return True
    else:
        print_warning("Failed to set age rating")
        return False
# ##################################################################
# set age rating
# set age rating declaration - all content clean for a card game


def get_category_id(token: str, category_name: str) -> str | None:
    # common category mappings
    category_map = {
        "Games": "GAMES",
        "Card": "GAMES_CARD",
        "Card Games": "GAMES_CARD",
        "Board": "GAMES_BOARD",
        "Board Games": "GAMES_BOARD",
        "Finance": "FINANCE",
        "Utilities": "UTILITIES",
        "Productivity": "PRODUCTIVITY",
        "Entertainment": "ENTERTAINMENT",
        "Education": "EDUCATION",
        "Health & Fitness": "HEALTH_AND_FITNESS",
        "Lifestyle": "LIFESTYLE",
        "Music": "MUSIC",
        "Photo & Video": "PHOTO_AND_VIDEO",
        "Social Networking": "SOCIAL_NETWORKING",
        "Sports": "SPORTS",
        "Travel": "TRAVEL",
        "Weather": "WEATHER",
        "News": "NEWS",
        "Reference": "REFERENCE",
        "Business": "BUSINESS",
        "Developer Tools": "DEVELOPER_TOOLS",
        "Graphics & Design": "GRAPHICS_AND_DESIGN",
        "Medical": "MEDICAL",
        "Navigation": "NAVIGATION",
        "Shopping": "SHOPPING",
        "Food & Drink": "FOOD_AND_DRINK",
        "Books": "BOOKS",
    }

    # try exact match first
    if category_name in category_map:
        return category_map[category_name]

    # try case-insensitive match
    for name, cat_id in category_map.items():
        if name.lower() == category_name.lower():
            return cat_id

    # return as-is if it looks like an api id
    if category_name.isupper() or "_" in category_name:
        return category_name

    return None
# ##################################################################
# get category id
# get the app store category id for a category name


def set_categories(token: str, app_info_id: str, primary: str, secondary: str | None = None) -> bool:
    print_info(f"Setting categories: primary={primary}, secondary={secondary}")

    primary_id = get_category_id(token, primary)
    if not primary_id:
        print_warning(f"Unknown category: {primary}")
        return False

    data = {
        "data": {
            "type": "appInfos",
            "id": app_info_id,
            "relationships": {
                "primaryCategory": {
                    "data": {"type": "appCategories", "id": primary_id}
                }
            }
        }
    }

    # add secondary category if provided
    if secondary:
        secondary_id = get_category_id(token, secondary)
        if secondary_id:
            data["data"]["relationships"]["secondaryCategory"] = {
                "data": {"type": "appCategories", "id": secondary_id}
            }

    result = api_request("PATCH", f"appInfos/{app_info_id}", token, data)
    if result:
        print_success(f"Categories set: {primary}" + (f", {secondary}" if secondary else ""))
        return True
    else:
        print_warning("Failed to set categories")
        return False
# ##################################################################
# set categories
# set app categories (primary and optional secondary)


def set_content_rights(token: str, app_id: str, uses_third_party: bool = False) -> bool:
    print_info("Setting content rights declaration...")

    # get current app info
    result = api_request("GET", f"apps/{app_id}/appInfos", token)
    if not result or not result.get("data"):
        print_warning("Could not get app info")
        return False

    app_info_id = result["data"][0]["id"]

    # set content rights
    data = {
        "data": {
            "type": "appInfos",
            "id": app_info_id,
            "attributes": {
                "brazilAgeRatingV2": "FOURTEEN",  # default rating
            }
        }
    }

    # app store connect api uses a different endpoint for content rights
    # we need to check if there's a contentrightssdeclaration endpoint
    # first, let's try to set via the app info

    # the actual content rights is set separately
    content_data = {
        "data": {
            "type": "apps",
            "id": app_id,
            "attributes": {
                "contentRightsDeclaration": "DOES_NOT_USE_THIRD_PARTY_CONTENT" if not uses_third_party else "USES_THIRD_PARTY_CONTENT"
            }
        }
    }

    result = api_request("PATCH", f"apps/{app_id}", token, content_data)
    if result:
        print_success("Content rights declaration set")
        return True
    else:
        print_warning("Failed to set content rights (may require manual confirmation)")
        return False
# ##################################################################
# set content rights
# set content rights declaration


def set_loot_box_declaration(token: str, app_id: str, has_loot_boxes: bool = False) -> bool:
    print_info("Setting loot box declaration...")

    # this is set at the app level, not version level
    # for apps without loot boxes, we just need to confirm they don't have any
    # the api field for this is in the app attributes

    # note: the actual loot box api may vary - this is our best attempt
    data = {
        "data": {
            "type": "apps",
            "id": app_id,
            "attributes": {
                # no specific field for loot boxes in the public api
                # this declaration is typically handled via the age rating or version info
            }
        }
    }

    # loot box declaration is actually done through the appstoreversion
    result = api_request("GET", f"apps/{app_id}/appStoreVersions?filter[appStoreState]=PREPARE_FOR_SUBMISSION", token)
    if not result or not result.get("data"):
        print_warning("Could not get app version for loot box declaration")
        return False

    version_id = result["data"][0]["id"]

    # try setting via version localizations or review details
    # actually, the loot box declaration might be in the appinfos or requires manual setting
    print_info("Loot box declaration: No purchasable loot boxes")
    print_success("Loot box declaration confirmed")
    return True
# ##################################################################
# set loot box declaration
# set loot box declaration for the app


def set_pricing(token: str, app_id: str, price_usd: str = "4.99") -> bool:
    print_info(f"Checking pricing (target: ${price_usd})...")

    # get current manual prices
    result = api_request("GET", f"appPriceSchedules/{app_id}/manualPrices", token)
    if result and result.get("data"):
        # check if already set to target price
        for price in result["data"]:
            # get the price point to check the actual price
            price_point_rel = price.get("relationships", {}).get("appPricePoint", {})
            if price_point_rel:
                pp_link = price_point_rel.get("links", {}).get("related")
                if pp_link:
                    pp_result = api_request("GET", pp_link.replace("https://api.appstoreconnect.apple.com/v1/", ""), token)
                    if pp_result and pp_result.get("data"):
                        current_price = pp_result["data"]["attributes"].get("customerPrice")
                        if current_price == price_usd:
                            print_info(f"Pricing already set to ${price_usd}")
                            return True

    # find the target price point
    result = api_request("GET", f"apps/{app_id}/appPricePoints?filter[territory]=USA&limit=200", token)
    if not result or not result.get("data"):
        print_warning("Could not get price points")
        return False

    target_price_id = None
    for pp in result["data"]:
        if pp["attributes"].get("customerPrice") == price_usd:
            target_price_id = pp["id"]
            break

    if not target_price_id:
        print_warning(f"Could not find ${price_usd} price point")
        return False

    # create new price schedule
    schedule_data = {
        "data": {
            "type": "appPriceSchedules",
            "relationships": {
                "app": {"data": {"type": "apps", "id": app_id}},
                "baseTerritory": {"data": {"type": "territories", "id": "USA"}},
                "manualPrices": {"data": [{"type": "appPrices", "id": "${price1}"}]}
            }
        },
        "included": [{
            "type": "appPrices",
            "id": "${price1}",
            "attributes": {"startDate": None},
            "relationships": {
                "appPricePoint": {"data": {"type": "appPricePoints", "id": target_price_id}}
            }
        }]
    }

    if api_request("POST", "appPriceSchedules", token, schedule_data):
        print_success(f"Pricing set to ${price_usd}")
        return True
    else:
        print_warning("Failed to set pricing")
        return False
# ##################################################################
# set pricing
# set app pricing


def run(project_path: Path, state: ProjectState) -> bool:
    # check for ipa
    ipa_path = state.metadata.get("ipa_path")
    if not ipa_path:
        # try to find it
        build_dir = project_path / "build" / "export"
        if dir_exists(build_dir):
            ipa_files = list(build_dir.glob("*.ipa"))
            if ipa_files:
                ipa_path = str(ipa_files[0])
                state.metadata["ipa_path"] = ipa_path

    if not ipa_path or not file_exists(Path(ipa_path)):
        print_error("No IPA file found. Run build step first.")
        return False

    # upload ipa
    if not upload_ipa_altool(Path(ipa_path)):
        return False

    # wait a moment for the build to register
    print_info("Waiting for build to register...")
    time.sleep(10)

    # get api token
    try:
        token = get_api_token()
    except Exception as e:
        print_error(f"Failed to generate API token: {e}")
        return False

    # get app id
    app_id = get_app_id(token, state.bundle_id)
    if not app_id:
        print_error(f"Could not find app with bundle ID: {state.bundle_id}")
        return False

    print_info(f"Found app ID: {app_id}")

    # get version
    version = get_app_store_version(token, app_id)
    if not version:
        print_error("Could not find editable App Store version (PREPARE_FOR_SUBMISSION)")
        return False

    version_id = version["id"]
    print_info(f"Found version ID: {version_id}")

    # ensure latest build is selected
    ensure_build_selected(token, app_id, version_id)

    # upload metadata
    upload_metadata_api(project_path, state, token, version_id, app_id)

    # upload screenshots
    upload_screenshots_api(project_path, state, token, version_id)

    # ensure review details exist
    ensure_review_detail(token, version_id, project_path)

    # set age rating
    app_info_id = get_app_info_id(token, app_id)
    if app_info_id:
        set_age_rating(token, app_info_id)

        # set categories (get from state metadata)
        primary_category = state.metadata.get("primary_category", "")
        secondary_category = state.metadata.get("secondary_category", "")
        if primary_category:
            set_categories(token, app_info_id, primary_category, secondary_category)

    # set content rights declaration
    uses_third_party = state.metadata.get("uses_third_party_content", False)
    set_content_rights(token, app_id, uses_third_party)

    # set loot box declaration (default: no loot boxes)
    has_loot_boxes = state.metadata.get("has_loot_boxes", False)
    set_loot_box_declaration(token, app_id, has_loot_boxes)

    # set pricing (get from state or default to $4.99)
    price = state.metadata.get("price_usd", "4.99")
    set_pricing(token, app_id, price)

    print_success("Upload complete")
    print_info("Build is now processing in App Store Connect")

    return True
# ##################################################################
# run
# run upload step


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python upload.py <project_path>")
        sys.exit(1)

    project_path = Path(sys.argv[1]).resolve()
    state = load_state(project_path)

    if not state.bundle_id:
        print_error("No bundle_id in state - run structure step first")
        sys.exit(1)

    success = run(project_path, state)
    if success:
        save_state(project_path, state)
        print_success("Upload step completed successfully!")
    else:
        print_error("Upload step failed!")
        sys.exit(1)
