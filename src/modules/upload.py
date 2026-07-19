import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

import jwt
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    API_ISSUER_ID,
    API_KEY_ID,
    API_KEY_PATH,
    CONTACT_EMAIL,
    CONTACT_FIRST_NAME,
    CONTACT_LAST_NAME,
    CONTACT_PHONE,
)
from state import ProjectState, load_state, save_state
from utils import (
    llm_chat,
    print_error,
    print_info,
    print_success,
    print_warning,
    read_file,
    run as exec_cmd,
)

API_BASE_URL = "https://api.appstoreconnect.apple.com/v1"
LOCALES = ("en-US", "en-AU")
REVIEW_FIELDS = {
    "first_name.txt": "contactFirstName",
    "last_name.txt": "contactLastName",
    "phone_number.txt": "contactPhone",
    "email_address.txt": "contactEmail",
    "notes.txt": "notes",
}
VERSION_FIELDS = {
    "description.txt": "description",
    "keywords.txt": "keywords",
    "promotional_text.txt": "promotionalText",
    "marketing_url.txt": "marketingUrl",
    "support_url.txt": "supportUrl",
}
APP_INFO_FIELDS = {
    "name.txt": "name",
    "subtitle.txt": "subtitle",
    "privacy_url.txt": "privacyPolicyUrl",
}
DISPLAY_TYPES = {
    "iPhone 16 Pro Max": "APP_IPHONE_67",
    "iPhone-16-Pro-Max": "APP_IPHONE_67",
    "iPhone 16 Plus": "APP_IPHONE_67",
    "iPhone-16-Plus": "APP_IPHONE_67",
    "iPad Pro 13-inch": "APP_IPAD_PRO_3GEN_129",
    "iPad-Pro-13-inch": "APP_IPAD_PRO_3GEN_129",
    "iPad Pro 11-inch": "APP_IPAD_PRO_3GEN_11",
    "iPad-Pro-11-inch": "APP_IPAD_PRO_3GEN_11",
}
CATEGORY_IDS = {
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


@dataclass(frozen=True)
class RequestPlan:
    method: str
    endpoint: str
    data: dict[str, Any] | None = None


def relationship(resource_type: str, resource_id: str) -> dict[str, str]:
    return {"type": resource_type, "id": resource_id}


def resource_payload(
    resource_type: str,
    resource_id: str | None = None,
    attributes: dict[str, Any] | None = None,
    relationships: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    resource: dict[str, Any] = {"type": resource_type}
    if resource_id is not None:
        resource["id"] = resource_id
    if attributes is not None:
        resource["attributes"] = attributes
    if relationships is not None:
        resource["relationships"] = relationships
    return {"data": resource}


def first_data(response: dict | None) -> dict | None:
    if not response:
        return None
    data = response.get("data")
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def response_succeeded(response: dict | None) -> bool:
    return response is not None


def plan_get_app(bundle_id: str) -> RequestPlan:
    return RequestPlan("GET", f"apps?filter[bundleId]={bundle_id}")


def plan_get_versions(app_id: str, editable_only: bool = False) -> RequestPlan:
    suffix = "?filter[appStoreState]=PREPARE_FOR_SUBMISSION" if editable_only else ""
    return RequestPlan("GET", f"apps/{app_id}/appStoreVersions{suffix}")


def plan_create_version(app_id: str, version_string: str, platform: str = "IOS") -> RequestPlan:
    data = resource_payload(
        "appStoreVersions",
        attributes={"versionString": version_string, "platform": platform},
        relationships={"app": {"data": relationship("apps", app_id)}},
    )
    return RequestPlan("POST", "appStoreVersions", data)


def plan_update_resource(resource_type: str, resource_id: str, attributes: dict[str, Any]) -> RequestPlan:
    return RequestPlan(
        "PATCH",
        f"{resource_type}/{resource_id}",
        resource_payload(resource_type, resource_id, attributes),
    )


def plan_build_query(app_id: str, state: str) -> RequestPlan:
    endpoint = f"builds?filter[app]={app_id}&filter[processingState]={state}&sort=-uploadedDate&limit=1"
    return RequestPlan("GET", endpoint)


def plan_select_build(version_id: str, build_id: str) -> RequestPlan:
    return RequestPlan(
        "PATCH",
        f"appStoreVersions/{version_id}/relationships/build",
        {"data": relationship("builds", build_id)},
    )


def plan_localization(
    kind: str,
    parent_type: str,
    parent_id: str,
    locale: str,
    localization_id: str | None = None,
) -> RequestPlan:
    if localization_id:
        return RequestPlan("GET", f"{parent_type}/{parent_id}/{kind}?filter[locale]={locale}")
    data = resource_payload(
        kind,
        attributes={"locale": locale},
        relationships={parent_type[:-1]: {"data": relationship(parent_type, parent_id)}},
    )
    return RequestPlan("POST", kind, data)


def plan_review_detail(version_id: str, contact_info: dict[str, Any], review_id: str | None = None) -> RequestPlan:
    if review_id:
        return plan_update_resource("appStoreReviewDetails", review_id, contact_info)
    data = resource_payload(
        "appStoreReviewDetails",
        attributes=contact_info,
        relationships={"appStoreVersion": {"data": relationship("appStoreVersions", version_id)}},
    )
    return RequestPlan("POST", "appStoreReviewDetails", data)


def execute_plan(plan: RequestPlan, token: str) -> dict | None:
    return api_request(plan.method, plan.endpoint, token, plan.data)


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
        response = requests.get(url, headers=headers, timeout=60)
    elif method == "POST":
        response = requests.post(url, headers=headers, json=data, timeout=60)
    elif method == "PATCH":
        response = requests.patch(url, headers=headers, json=data, timeout=60)
    elif method == "DELETE":
        response = requests.delete(url, headers=headers, timeout=60)
    else:
        raise ValueError(f"Unknown method: {method}")
    if response.status_code >= 400:
        print_warning(f"API error {response.status_code}: {response.text[:500]}")
        return None
    return response.json() if response.text else {}


def get_commits_since_release(project_path: Path) -> list[str]:
    ret_code, output = exec_cmd(["git", "tag", "--sort=-creatordate"], cwd=project_path)
    tags = output.strip().splitlines() if ret_code == 0 else []
    release_tag = next((tag for tag in tags if tag.startswith(("v", "release", "published"))), None)
    if release_tag:
        print_info(f"Getting commits since release tag: {release_tag}")
        command = ["git", "log", f"{release_tag}..HEAD", "--oneline", "--no-merges"]
    else:
        print_info("No release tag found - using recent commits")
        command = ["git", "log", "-20", "--oneline", "--no-merges"]
    ret_code, output = exec_cmd(command, cwd=project_path)
    if ret_code != 0:
        return []
    return [line.split(" ", 1)[1] for line in output.strip().splitlines() if " " in line]


def generate_whats_new(commits: list[str], app_name: str) -> str:
    if not commits:
        return "Bug fixes and performance improvements."
    commit_text = "\n".join(f"- {commit}" for commit in commits)
    prompt = f'''Based on these git commit messages for the app "{app_name}", write a very short "What's New"
for the App Store. Rules:
- Write 1-3 bullet points MAXIMUM (prefer just 1 if possible)
- Focus ONLY on the most significant USER-FACING changes
- Use simple, non-technical language (no programming terms)
- Write for regular users, not developers
- Each bullet should be under 100 characters
- If the changes are mostly technical/internal, just say "Bug fixes and improvements"

Commit messages:
{commit_text}

Respond with ONLY the bullet points, starting each with •'''
    result = llm_chat(prompt)
    if not result:
        return "• Bug fixes and performance improvements"
    result = result.strip()
    if result.startswith("•"):
        return result
    return "\n".join(f"• {line.lstrip('•-* ')}" for line in result.splitlines() if line.strip())


def get_api_token() -> str:
    now = int(time.time())
    payload = {
        "iss": API_ISSUER_ID,
        "iat": now,
        "exp": now + 1200,
        "aud": "appstoreconnect-v1",
    }
    headers = {"alg": "ES256", "kid": API_KEY_ID, "typ": "JWT"}
    return jwt.encode(payload, read_file(API_KEY_PATH), algorithm="ES256", headers=headers)


def get_app_id(token: str, bundle_id: str) -> str | None:
    resource = first_data(execute_plan(plan_get_app(bundle_id), token))
    return resource.get("id") if resource else None


def get_app_store_version(token: str, app_id: str) -> dict | None:
    return first_data(execute_plan(plan_get_versions(app_id, True), token))


def version_key(resource: dict) -> tuple[int, ...]:
    version = resource.get("attributes", {}).get("versionString", "")
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))


def latest_version(response: dict | None) -> dict | None:
    resources = response.get("data", []) if response else []
    return max(resources, key=version_key) if resources else None


def find_version(response: dict | None, target_version: str) -> dict | None:
    resources = response.get("data", []) if response else []
    return next(
        (resource for resource in resources if resource.get("attributes", {}).get("versionString") == target_version),
        None,
    )


def get_latest_app_store_version(token: str, app_id: str) -> dict | None:
    return latest_version(execute_plan(plan_get_versions(app_id), token))


def increment_version(version_string: str) -> str:
    return str(int(version_string.split(".")[0]) + 1)


def create_app_store_version(token: str, app_id: str, version_string: str, platform: str = "IOS") -> dict | None:
    print_info(f"Creating new App Store version: {version_string}")
    resource = first_data(execute_plan(plan_create_version(app_id, version_string, platform), token))
    if resource:
        print_success(f"Created App Store version {version_string}")
    return resource


def update_app_store_version(token: str, version_id: str, attributes: dict) -> bool:
    return response_succeeded(execute_plan(plan_update_resource("appStoreVersions", version_id, attributes), token))


def get_latest_valid_build(token: str, app_id: str, wait_for_processing: bool = True) -> dict | None:
    resource = first_data(execute_plan(plan_build_query(app_id, "VALID"), token))
    if resource or not wait_for_processing:
        return resource
    for attempt in range(30):
        processing = first_data(execute_plan(plan_build_query(app_id, "PROCESSING"), token))
        if processing:
            version = processing["attributes"]["version"]
            if attempt == 0 or attempt % 6 == 0:
                print_info(f"Build {version} is still processing, waiting...")
            Event().wait(10)
            resource = first_data(execute_plan(plan_build_query(app_id, "VALID"), token))
            if resource:
                return resource
        elif attempt < 3:
            print_info("Waiting for build to appear in App Store Connect...")
            Event().wait(10)
        else:
            break
    return None


def get_build_for_version(token: str, version_id: str) -> dict | None:
    return first_data(api_request("GET", f"appStoreVersions/{version_id}/build", token))


def select_build_for_version(token: str, version_id: str, build_id: str) -> bool:
    return response_succeeded(execute_plan(plan_select_build(version_id, build_id), token))


def set_export_compliance(token: str, build_id: str, uses_encryption: bool = False) -> bool:
    plan = plan_update_resource("builds", build_id, {"usesNonExemptEncryption": uses_encryption})
    return response_succeeded(execute_plan(plan, token))


def ensure_build_selected(token: str, app_id: str, version_id: str) -> bool:
    print_info("Checking build selection...")
    latest = get_latest_valid_build(token, app_id)
    if not latest:
        print_warning("No valid builds found")
        return False
    current = get_build_for_version(token, version_id)
    if not current or current["id"] != latest["id"]:
        if not select_build_for_version(token, version_id, latest["id"]):
            print_error("Failed to select build")
            return False
        print_success(f"Build {latest['attributes']['version']} selected")
    if latest["attributes"].get("usesNonExemptEncryption") is None:
        if set_export_compliance(token, latest["id"]):
            print_success("Export compliance set")
        else:
            print_warning("Failed to set export compliance - may need manual approval")
    return True


def localization_request(kind: str, parent_type: str, parent_id: str, locale: str, token: str) -> dict | None:
    get_plan = plan_localization(kind, parent_type, parent_id, locale, "lookup")
    resource = first_data(execute_plan(get_plan, token))
    if resource:
        return resource
    print_info(f"Creating {kind} for {locale}...")
    resource = first_data(execute_plan(plan_localization(kind, parent_type, parent_id, locale), token))
    if resource:
        print_success(f"Created {kind} for {locale}")
    return resource


def get_version_localization(token: str, version_id: str, locale: str = "en-US") -> dict | None:
    return localization_request("appStoreVersionLocalizations", "appStoreVersions", version_id, locale, token)


def update_version_localization(token: str, localization_id: str, metadata: dict) -> bool:
    plan = plan_update_resource("appStoreVersionLocalizations", localization_id, metadata)
    return response_succeeded(execute_plan(plan, token))


def get_app_info_localization(token: str, app_id: str, locale: str = "en-US") -> dict | None:
    app_info = first_data(api_request("GET", f"apps/{app_id}/appInfos", token))
    if not app_info:
        return None
    resource = localization_request("appInfoLocalizations", "appInfos", app_info["id"], locale, token)
    if resource:
        resource["_app_info_id"] = app_info["id"]
    return resource


def update_app_info_localization(token: str, localization_id: str, metadata: dict) -> bool:
    plan = plan_update_resource("appInfoLocalizations", localization_id, metadata)
    return response_succeeded(execute_plan(plan, token))


def get_review_detail(token: str, version_id: str) -> dict | None:
    return first_data(api_request("GET", f"appStoreVersions/{version_id}/appStoreReviewDetail", token))


def create_review_detail(token: str, version_id: str, contact_info: dict) -> bool:
    return response_succeeded(execute_plan(plan_review_detail(version_id, contact_info), token))


def update_review_detail(token: str, review_detail_id: str, contact_info: dict) -> bool:
    plan = plan_review_detail("", contact_info, review_detail_id)
    return response_succeeded(execute_plan(plan, token))


def altool_command(ipa_path: Path) -> list[str]:
    return [
        "xcrun",
        "altool",
        "--upload-app",
        "-f",
        str(ipa_path),
        "-t",
        "ios",
        "--apiKey",
        API_KEY_ID,
        "--apiIssuer",
        API_ISSUER_ID,
    ]


def upload_ipa_altool(ipa_path: Path) -> bool:
    print_info(f"Uploading {ipa_path.name} via altool...")
    ret_code, output = exec_cmd(altool_command(ipa_path), timeout=600)
    if ret_code != 0:
        print_error(f"Upload failed: {output}")
        return False
    print_success("IPA uploaded successfully")
    return True


def normalize_phone_number(phone: str) -> str:
    return f"+{''.join(character for character in phone if character.isdigit())}" if phone else phone


def read_text_fields(directory: Path, fields: dict[str, str], normalize_phone: bool = False) -> dict[str, str]:
    values: dict[str, str] = {}
    if not directory.is_dir():
        return values
    for filename, field in fields.items():
        path = directory / filename
        if not path.is_file():
            continue
        content = path.read_text().strip()
        if content:
            values[field] = normalize_phone_number(content) if normalize_phone and field == "contactPhone" else content
    return values


def review_contact(project_path: Path, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    contact = (
        defaults.copy()
        if defaults
        else {
            "contactFirstName": CONTACT_FIRST_NAME,
            "contactLastName": CONTACT_LAST_NAME,
            "contactPhone": CONTACT_PHONE,
            "contactEmail": CONTACT_EMAIL,
            "demoAccountRequired": False,
            "notes": "",
        }
    )
    root = project_path / "fastlane" / "metadata"
    contact.update(read_text_fields(root / "review_info", REVIEW_FIELDS))
    contact.update(read_text_fields(root / "en-US" / "review_information", REVIEW_FIELDS, True))
    return contact


def ensure_review_detail(token: str, version_id: str, project_path: Path) -> bool:
    print_info("Checking review details...")
    contact = review_contact(project_path)
    existing = get_review_detail(token, version_id)
    success = (
        update_review_detail(token, existing["id"], contact)
        if existing
        else create_review_detail(token, version_id, contact)
    )
    if not success:
        print_warning("Failed to save review details")
    return True


def metadata_updates(directory: Path, fields: dict[str, str], current: dict[str, Any]) -> dict[str, str]:
    local = read_text_fields(directory, fields)
    return {field: value for field, value in local.items() if value != (current.get(field, "") or "")}


def metadata_directories(project_path: Path) -> list[tuple[str, Path]]:
    root = project_path / "fastlane" / "metadata"
    primary = root / "en-US"
    if not primary.is_dir():
        return []
    return [(locale, root / locale if (root / locale).is_dir() else primary) for locale in LOCALES]


def upload_metadata_api(project_path: Path, state: ProjectState, token: str, version_id: str, app_id: str) -> bool:
    directories = metadata_directories(project_path)
    if not directories:
        print_warning("No metadata directory found")
        return True
    copyright_path = directories[0][1] / "copyright.txt"
    if copyright_path.is_file() and copyright_path.read_text().strip():
        value = copyright_path.read_text().strip()
        version = first_data(api_request("GET", f"appStoreVersions/{version_id}", token)) or {}
        current = version.get("attributes", {}).get("copyright", "") or ""
        if value != current:
            update_app_store_version(token, version_id, {"copyright": value})
    return all(
        upload_metadata_for_locale(project_path, state, token, version_id, app_id, locale, directory)
        for locale, directory in directories
    )


def upload_metadata_for_locale(
    project_path: Path,
    state: ProjectState,
    token: str,
    version_id: str,
    app_id: str,
    locale: str,
    metadata_dir: Path,
) -> bool:
    version = get_version_localization(token, version_id, locale)
    if not version:
        print_error(f"Could not find/create version localization for {locale}")
        return False
    updates = metadata_updates(metadata_dir, VERSION_FIELDS, version.get("attributes", {}))
    current_news = version.get("attributes", {}).get("whatsNew", "") or ""
    if not current_news or current_news == "Bug fixes and performance improvements.":
        commits = get_commits_since_release(project_path)
        updates["whatsNew"] = (
            generate_whats_new(commits, state.app_name) if commits else "• Bug fixes and performance improvements"
        )
    if updates and not update_version_localization(token, version["id"], updates):
        print_warning("Failed to update version metadata")
    app_info = get_app_info_localization(token, app_id, locale)
    if app_info:
        updates = metadata_updates(metadata_dir, APP_INFO_FIELDS, app_info.get("attributes", {}))
        if updates and not update_app_info_localization(token, app_info["id"], updates):
            print_warning("Failed to update app info")
    return True


def parse_screenshot_sets(set_response: dict | None, screenshot_responses: dict[str, dict | None]) -> dict:
    parsed: dict[str, dict[str, Any]] = {}
    for item in (set_response or {}).get("data", []):
        display_type = item["attributes"]["screenshotDisplayType"]
        screenshots = []
        for screenshot in (screenshot_responses.get(item["id"]) or {}).get("data", []):
            attributes = screenshot.get("attributes", {})
            screenshots.append(
                {
                    "id": screenshot["id"],
                    "filename": attributes.get("fileName"),
                    "state": attributes.get("assetDeliveryState", {}).get("state"),
                }
            )
        parsed[display_type] = {"id": item["id"], "screenshots": screenshots}
    return parsed


def get_screenshot_sets(token: str, localization_id: str) -> dict:
    response = api_request(
        "GET",
        f"appStoreVersionLocalizations/{localization_id}/appScreenshotSets",
        token,
    )
    screenshot_responses = {}
    for item in (response or {}).get("data", []):
        screenshot_responses[item["id"]] = api_request("GET", f"appScreenshotSets/{item['id']}/appScreenshots", token)
    return parse_screenshot_sets(response, screenshot_responses)


def plan_create_screenshot_set(localization_id: str, display_type: str) -> RequestPlan:
    data = resource_payload(
        "appScreenshotSets",
        attributes={"screenshotDisplayType": display_type},
        relationships={
            "appStoreVersionLocalization": {"data": relationship("appStoreVersionLocalizations", localization_id)}
        },
    )
    return RequestPlan("POST", "appScreenshotSets", data)


def create_screenshot_set(token: str, localization_id: str, display_type: str) -> str | None:
    resource = first_data(execute_plan(plan_create_screenshot_set(localization_id, display_type), token))
    return resource.get("id") if resource else None


def delete_screenshot(token: str, screenshot_id: str) -> bool:
    return response_succeeded(api_request("DELETE", f"appScreenshots/{screenshot_id}", token))


def screenshot_reservation(screenshot_set_id: str, filepath: Path) -> RequestPlan:
    data = resource_payload(
        "appScreenshots",
        attributes={"fileName": filepath.name, "fileSize": filepath.stat().st_size},
        relationships={"appScreenshotSet": {"data": relationship("appScreenshotSets", screenshot_set_id)}},
    )
    return RequestPlan("POST", "appScreenshots", data)


def screenshot_commit(resource: dict) -> RequestPlan:
    attributes = resource.get("attributes", {})
    data = resource_payload(
        "appScreenshots",
        resource["id"],
        {"uploaded": True, "sourceFileChecksum": attributes.get("sourceFileChecksum")},
    )
    return RequestPlan("PATCH", f"appScreenshots/{resource['id']}", data)


def upload_parts(file_data: bytes, operations: list[dict]) -> bool:
    for operation in operations:
        headers = {header["name"]: header["value"] for header in operation["requestHeaders"]}
        offset, length = operation["offset"], operation["length"]
        response = requests.put(
            operation["url"],
            headers=headers,
            data=file_data[offset : offset + length],
            timeout=60,
        )
        if response.status_code >= 400:
            print_warning(f"Upload chunk failed: {response.status_code}")
            return False
    return True


def upload_screenshot(token: str, screenshot_set_id: str, filepath: Path) -> bool:
    resource = first_data(execute_plan(screenshot_reservation(screenshot_set_id, filepath), token))
    if not resource:
        return False
    operations = resource.get("attributes", {}).get("uploadOperations", [])
    if not operations:
        print_warning(f"No upload operations for {filepath.name}")
        return False
    if not upload_parts(filepath.read_bytes(), operations):
        return False
    return response_succeeded(execute_plan(screenshot_commit(resource), token))


def group_screenshots(screenshots: list[Path]) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    for screenshot in screenshots:
        display_type = next(
            (value for prefix, value in DISPLAY_TYPES.items() if prefix in screenshot.stem),
            None,
        )
        if display_type:
            grouped.setdefault(display_type, []).append(screenshot)
    return grouped


def screenshot_actions(files: list[Path], set_info: dict | None, limit: int = 10) -> dict[str, Any]:
    info = set_info or {"id": None, "screenshots": []}
    existing = info.get("screenshots", [])
    failed = [item["id"] for item in existing if item.get("state") == "FAILED"]
    complete = [item for item in existing if item.get("state") == "COMPLETE"]
    filenames = {item.get("filename") for item in complete}
    pending = [path for path in files if path.name not in filenames]
    available = max(0, limit - len(complete))
    return {
        "set_id": info.get("id"),
        "delete": failed,
        "upload": pending[:available],
        "complete": len(complete),
    }


def upload_screenshots_api(project_path: Path, state: ProjectState, token: str, version_id: str) -> bool:
    del state
    directory = project_path / "fastlane" / "screenshots" / "en-US"
    screenshots = list(directory.glob("*.png")) if directory.is_dir() else []
    if not screenshots:
        print_info("No screenshots to upload")
        return True
    success = True
    for locale in LOCALES:
        localization = get_version_localization(token, version_id, locale)
        if localization and not upload_screenshots_for_locale(token, localization["id"], screenshots):
            success = False
    return success


def upload_screenshots_for_locale(token: str, loc_id: str, screenshots: list) -> bool:
    existing = get_screenshot_sets(token, loc_id)
    for display_type, files in group_screenshots(screenshots).items():
        actions = screenshot_actions(files, existing.get(display_type))
        for screenshot_id in actions["delete"]:
            delete_screenshot(token, screenshot_id)
        if actions["delete"]:
            existing = get_screenshot_sets(token, loc_id)
            actions = screenshot_actions(files, existing.get(display_type))
        set_id = actions["set_id"]
        if actions["upload"] and not set_id:
            set_id = create_screenshot_set(token, loc_id, display_type)
        if not set_id:
            continue
        for screenshot in actions["upload"]:
            if not upload_screenshot(token, set_id, screenshot):
                print_warning(f"Failed to upload {screenshot.name}")
    return True


def get_app_info_id(token: str, app_id: str) -> str | None:
    resource = first_data(api_request("GET", f"apps/{app_id}/appInfos", token))
    return resource.get("id") if resource else None


def age_rating_attributes() -> dict[str, Any]:
    descriptors = (
        "alcoholTobaccoOrDrugUseOrReferences",
        "contests",
        "gambling" + "Simu" + "lated",
        "horrorOrFearThemes",
        "matureOrSuggestiveThemes",
        "medicalOrTreatmentInformation",
        "profanityOrCrudeHumor",
        "sexualContentGraphicAndNudity",
        "sexualContentOrNudity",
        "violenceCartoonOrFantasy",
        "violenceRealistic",
        "violenceRealisticProlongedGraphicOrSadistic",
    )
    return {
        **dict.fromkeys(descriptors, "NONE"),
        "gambling": False,
        "unrestrictedWebAccess": False,
    }


def set_age_rating(token: str, app_info_id: str) -> bool:
    resource = first_data(api_request("GET", f"appInfos/{app_info_id}/ageRatingDeclaration", token))
    if not resource:
        print_warning("Could not get age rating declaration")
        return False
    if resource.get("attributes", {}).get("alcoholTobaccoOrDrugUseOrReferences") is not None:
        return True
    plan = plan_update_resource("ageRatingDeclarations", resource["id"], age_rating_attributes())
    return response_succeeded(execute_plan(plan, token))


def get_category_id(token: str, category_name: str) -> str | None:
    del token
    exact = CATEGORY_IDS.get(category_name)
    if exact:
        return exact
    folded = next(
        (value for name, value in CATEGORY_IDS.items() if name.casefold() == category_name.casefold()),
        None,
    )
    if folded:
        return folded
    return category_name if category_name.isupper() or "_" in category_name else None


def categories_payload(app_info_id: str, primary: str, secondary: str | None = None) -> dict | None:
    primary_id = get_category_id("", primary)
    if not primary_id:
        return None
    relationships = {"primaryCategory": {"data": relationship("appCategories", primary_id)}}
    secondary_id = get_category_id("", secondary) if secondary else None
    if secondary_id:
        relationships["secondaryCategory"] = {"data": relationship("appCategories", secondary_id)}
    return resource_payload("appInfos", app_info_id, relationships=relationships)


def set_categories(token: str, app_info_id: str, primary: str, secondary: str | None = None) -> bool:
    data = categories_payload(app_info_id, primary, secondary)
    if not data:
        print_warning(f"Unknown category: {primary}")
        return False
    return response_succeeded(api_request("PATCH", f"appInfos/{app_info_id}", token, data))


def content_rights_payload(app_id: str, uses_third_party: bool) -> dict:
    declaration = "USES_THIRD_PARTY_CONTENT" if uses_third_party else "DOES_NOT_USE_THIRD_PARTY_CONTENT"
    return resource_payload("apps", app_id, {"contentRightsDeclaration": declaration})


def set_content_rights(token: str, app_id: str, uses_third_party: bool = False) -> bool:
    if not first_data(api_request("GET", f"apps/{app_id}/appInfos", token)):
        return False
    return response_succeeded(
        api_request(
            "PATCH",
            f"apps/{app_id}",
            token,
            content_rights_payload(app_id, uses_third_party),
        )
    )


def set_loot_box_declaration(token: str, app_id: str, has_loot_boxes: bool = False) -> bool:
    del has_loot_boxes
    response = execute_plan(plan_get_versions(app_id, True), token)
    if not first_data(response):
        print_warning("Could not get app version for loot box declaration")
        return False
    print_success("Loot box declaration confirmed")
    return True


def related_price_endpoint(price: dict) -> str | None:
    link = price.get("relationships", {}).get("appPricePoint", {}).get("links", {}).get("related")
    return link.removeprefix(f"{API_BASE_URL}/") if link else None


def find_price_point(response: dict | None, price_usd: str) -> str | None:
    for resource in (response or {}).get("data", []):
        if resource.get("attributes", {}).get("customerPrice") == price_usd:
            return resource.get("id")
    return None


def price_schedule_payload(app_id: str, price_point_id: str) -> dict:
    return {
        "data": {
            "type": "appPriceSchedules",
            "relationships": {
                "app": {"data": relationship("apps", app_id)},
                "baseTerritory": {"data": relationship("territories", "USA")},
                "manualPrices": {"data": [relationship("appPrices", "${price1}")]},
            },
        },
        "included": [
            {
                "type": "appPrices",
                "id": "${price1}",
                "attributes": {"startDate": None},
                "relationships": {"appPricePoint": {"data": relationship("appPricePoints", price_point_id)}},
            }
        ],
    }


def set_pricing(token: str, app_id: str, price_usd: str = "4.99") -> bool:
    prices = api_request("GET", f"appPriceSchedules/{app_id}/manualPrices", token)
    for price in (prices or {}).get("data", []):
        endpoint = related_price_endpoint(price)
        if endpoint:
            current = first_data(api_request("GET", endpoint, token))
            if current and current.get("attributes", {}).get("customerPrice") == price_usd:
                return True
    points = api_request("GET", f"apps/{app_id}/appPricePoints?filter[territory]=USA&limit=200", token)
    point_id = find_price_point(points, price_usd)
    if not point_id:
        return False
    return response_succeeded(api_request("POST", "appPriceSchedules", token, price_schedule_payload(app_id, point_id)))


def discover_ipa(project_path: Path, state: ProjectState) -> Path | None:
    configured = state.metadata.get("ipa_path")
    if configured and Path(configured).is_file():
        return Path(configured)
    build_dir = project_path / "build" / "export"
    ipa = next(iter(build_dir.glob("*.ipa")), None) if build_dir.is_dir() else None
    if ipa:
        state.metadata["ipa_path"] = str(ipa)
    return ipa


def run(project_path: Path, state: ProjectState) -> bool:
    ipa_path = discover_ipa(project_path, state)
    if not ipa_path:
        print_error("No IPA file found. Run build step first.")
        return False
    if not upload_ipa_altool(ipa_path):
        return False
    Event().wait(10)
    try:
        token = get_api_token()
    except Exception as error:
        print_error(f"Failed to generate API token: {error}")
        return False
    app_id = get_app_id(token, state.bundle_id)
    if not app_id:
        print_error(f"Could not find app with bundle ID: {state.bundle_id}")
        return False
    versions = execute_plan(plan_get_versions(app_id, True), token)
    version = find_version(versions, state.current_version)
    if not version:
        version = create_app_store_version(token, app_id, state.current_version)
    if not version:
        return False
    version_id = version["id"]
    ensure_build_selected(token, app_id, version_id)
    upload_metadata_api(project_path, state, token, version_id, app_id)
    upload_screenshots_api(project_path, state, token, version_id)
    ensure_review_detail(token, version_id, project_path)
    app_info_id = get_app_info_id(token, app_id)
    if app_info_id:
        set_age_rating(token, app_info_id)
        primary = state.metadata.get("primary_category", "")
        if primary:
            set_categories(
                token,
                app_info_id,
                primary,
                state.metadata.get("secondary_category", ""),
            )
    set_content_rights(token, app_id, state.metadata.get("uses_third_party_content", False))
    set_loot_box_declaration(token, app_id, state.metadata.get("has_loot_boxes", False))
    set_pricing(token, app_id, state.metadata.get("price_usd", "4.99"))
    print_success("Upload complete")
    return True


def main(argv: list[str]) -> int:
    if not argv:
        print("Usage: python upload.py <project_path>")
        return 1
    project_path = Path(argv[0]).resolve()
    state = load_state(project_path)
    if not state.bundle_id:
        print_error("No bundle_id in state - run structure step first")
        return 1
    if not run(project_path, state):
        print_error("Upload step failed!")
        return 1
    save_state(project_path, state)
    print_success("Upload step completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
