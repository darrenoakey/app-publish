import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from modules.upload import (
    API_BASE_URL,
    APP_INFO_FIELDS,
    REVIEW_FIELDS,
    VERSION_FIELDS,
    RequestPlan,
    api_request,
    age_rating_attributes,
    altool_command,
    categories_payload,
    content_rights_payload,
    discover_ipa,
    find_price_point,
    find_version,
    first_data,
    generate_whats_new,
    get_commits_since_release,
    get_category_id,
    group_screenshots,
    increment_version,
    latest_version,
    main,
    metadata_directories,
    metadata_updates,
    normalize_phone_number,
    parse_screenshot_sets,
    plan_build_query,
    plan_create_screenshot_set,
    plan_create_version,
    plan_get_app,
    plan_get_versions,
    plan_localization,
    plan_review_detail,
    plan_select_build,
    plan_update_resource,
    price_schedule_payload,
    read_text_fields,
    related_price_endpoint,
    relationship,
    resource_payload,
    response_succeeded,
    review_contact,
    screenshot_actions,
    screenshot_commit,
    screenshot_reservation,
    upload_parts,
    version_key,
)
from state import ProjectState


class LocalJsonHandler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def log_message(self, message_format: str, *args: object) -> None:
        del message_format, args

    def send_json(self, status: int, content: dict | None) -> None:
        body = json.dumps(content).encode() if content is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def record(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        entry = {
            "method": self.command,
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": json.loads(body) if body else None,
            "bytes": body,
        }
        self.received.append(entry)
        return entry

    def do_GET(self) -> None:
        entry = self.record()
        if self.path == "/error":
            self.send_json(422, {"message": "invalid local request"})
        else:
            self.send_json(200, {"data": {"method": entry["method"], "path": entry["path"]}})

    def do_POST(self) -> None:
        entry = self.record()
        self.send_json(201, {"data": entry["body"]})

    def do_PATCH(self) -> None:
        entry = self.record()
        self.send_json(200, {"data": entry["body"]})

    def do_DELETE(self) -> None:
        self.record()
        self.send_json(204, None)

    def do_PUT(self) -> None:
        entry = self.record()
        status = 409 if self.path == "/reject" else 200
        self.send_json(status, {"size": len(entry["bytes"])})


@pytest.fixture
def local_json_service() -> tuple[str, type[LocalJsonHandler]]:
    LocalJsonHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalJsonHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", LocalJsonHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_resource_request_plans_preserve_app_store_shapes() -> None:
    assert relationship("apps", "app-1") == {"type": "apps", "id": "app-1"}
    assert resource_payload("apps") == {"data": {"type": "apps"}}
    assert resource_payload("apps", "app-1", {"name": "Reader"}) == {
        "data": {"type": "apps", "id": "app-1", "attributes": {"name": "Reader"}}
    }
    assert resource_payload("apps", relationships={"build": {"data": relationship("builds", "build-1")}}) == {
        "data": {
            "type": "apps",
            "relationships": {"build": {"data": {"type": "builds", "id": "build-1"}}},
        }
    }

    assert plan_get_app("com.example.reader") == RequestPlan("GET", "apps?filter[bundleId]=com.example.reader")
    assert plan_get_versions("app-1") == RequestPlan("GET", "apps/app-1/appStoreVersions")
    assert plan_get_versions("app-1", True).endpoint.endswith("PREPARE_FOR_SUBMISSION")
    assert plan_build_query("app-1", "VALID").endpoint == (
        "builds?filter[app]=app-1&filter[processingState]=VALID&sort=-uploadedDate&limit=1"
    )

    create = plan_create_version("app-1", "2.0", "IOS")
    assert create.method == "POST"
    assert create.endpoint == "appStoreVersions"
    assert create.data["data"]["attributes"] == {
        "versionString": "2.0",
        "platform": "IOS",
    }
    assert create.data["data"]["relationships"]["app"]["data"]["id"] == "app-1"

    update = plan_update_resource("builds", "build-1", {"usesNonExemptEncryption": False})
    assert update == RequestPlan(
        "PATCH",
        "builds/build-1",
        {
            "data": {
                "type": "builds",
                "id": "build-1",
                "attributes": {"usesNonExemptEncryption": False},
            }
        },
    )
    assert plan_select_build("version-1", "build-1") == RequestPlan(
        "PATCH",
        "appStoreVersions/version-1/relationships/build",
        {"data": {"type": "builds", "id": "build-1"}},
    )


def test_http_transport_and_chunk_upload_use_a_real_local_service(
    local_json_service: tuple[str, type[LocalJsonHandler]],
) -> None:
    base_url, handler = local_json_service
    assert api_request("GET", "items/one", "local-token", base_url=base_url) == {
        "data": {"method": "GET", "path": "/items/one"}
    }
    payload = {"name": "Reader", "version": 2}
    assert api_request("POST", "/items", "local-token", payload, base_url)["data"] == payload
    assert api_request("PATCH", "items/one", "local-token", payload, f"{base_url}/")["data"] == payload
    assert api_request("DELETE", "items/one", "local-token", base_url=base_url) == {}
    assert api_request("GET", "error", "local-token", base_url=base_url) is None
    assert all(entry["authorization"] == "Bearer local-token" for entry in handler.received[:5])
    with pytest.raises(ValueError, match="Unknown method: TRACE"):
        api_request("TRACE", "items", "local-token", base_url=base_url)

    operations = [
        {
            "url": f"{base_url}/part-one",
            "requestHeaders": [{"name": "X-Part", "value": "one"}],
            "offset": 1,
            "length": 3,
        },
        {
            "url": f"{base_url}/part-two",
            "requestHeaders": [],
            "offset": 4,
            "length": 2,
        },
    ]
    assert upload_parts(b"0123456789", operations) is True
    assert [entry["bytes"] for entry in handler.received[-2:]] == [b"123", b"45"]
    rejected = [{**operations[0], "url": f"{base_url}/reject"}]
    assert upload_parts(b"0123456789", rejected) is False


def test_localization_review_and_screenshot_request_plans(tmp_path: Path) -> None:
    lookup = plan_localization(
        "appStoreVersionLocalizations",
        "appStoreVersions",
        "version-1",
        "en-AU",
        "lookup",
    )
    assert lookup == RequestPlan(
        "GET",
        "appStoreVersions/version-1/appStoreVersionLocalizations?filter[locale]=en-AU",
    )
    create = plan_localization("appInfoLocalizations", "appInfos", "info-1", "en-US")
    assert create.method == "POST"
    assert create.data["data"]["attributes"] == {"locale": "en-US"}
    assert create.data["data"]["relationships"]["appInfo"]["data"]["id"] == "info-1"

    contact = {"contactEmail": "reviewer@example.com", "demoAccountRequired": False}
    review_create = plan_review_detail("version-1", contact)
    assert review_create.endpoint == "appStoreReviewDetails"
    assert review_create.data["data"]["relationships"]["appStoreVersion"]["data"]["id"] == "version-1"
    review_update = plan_review_detail("version-1", contact, "review-1")
    assert review_update.endpoint == "appStoreReviewDetails/review-1"
    assert review_update.data["data"]["attributes"] == contact

    set_plan = plan_create_screenshot_set("localization-1", "APP_IPHONE_67")
    assert set_plan.data["data"]["attributes"]["screenshotDisplayType"] == "APP_IPHONE_67"
    assert set_plan.data["data"]["relationships"]["appStoreVersionLocalization"]["data"]["id"] == "localization-1"

    image = tmp_path / "iPhone 16 Pro Max-01.png"
    image.write_bytes(b"abcdef")
    reservation = screenshot_reservation("set-1", image)
    assert reservation.data["data"]["attributes"] == {
        "fileName": image.name,
        "fileSize": 6,
    }
    assert reservation.data["data"]["relationships"]["appScreenshotSet"]["data"]["id"] == "set-1"

    resource = {
        "id": "shot-1",
        "attributes": {"sourceFileChecksum": "abc123", "uploadOperations": []},
    }
    commit = screenshot_commit(resource)
    assert commit.endpoint == "appScreenshots/shot-1"
    assert commit.data["data"]["attributes"] == {
        "uploaded": True,
        "sourceFileChecksum": "abc123",
    }


def test_response_and_version_parsing_handles_empty_and_mixed_versions() -> None:
    assert first_data(None) is None
    assert first_data({}) is None
    assert first_data({"data": []}) is None
    assert first_data({"data": [{"id": "one"}]}) == {"id": "one"}
    assert first_data({"data": {"id": "one"}}) == {"id": "one"}
    assert first_data({"data": "unexpected"}) is None
    assert response_succeeded({}) is True
    assert response_succeeded(None) is False

    versions = {
        "data": [
            {"id": "one", "attributes": {"versionString": "1.12.3"}},
            {"id": "two", "attributes": {"versionString": "2.0"}},
            {"id": "text", "attributes": {"versionString": "release.beta"}},
        ]
    }
    assert version_key(versions["data"][0]) == (1, 12, 3)
    assert version_key(versions["data"][2]) == (0, 0)
    assert latest_version(versions)["id"] == "two"
    assert latest_version(None) is None
    assert latest_version({"data": []}) is None
    assert find_version(versions, "1.12.3")["id"] == "one"
    assert find_version(versions, "9.0") is None
    assert find_version(None, "1.0") is None
    assert increment_version("12.9.4") == "13"
    with pytest.raises(ValueError):
        increment_version("release")


def test_commit_discovery_reads_a_real_local_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init")
    git("config", "user.name", "Upload Test")
    git("config", "user.email", "upload-test@example.com")
    history = repository / "history.txt"
    history.write_text("first\n")
    git("add", "history.txt")
    git("commit", "-m", "Initial release")
    git("tag", "v1.0")
    history.write_text("first\nsecond\n")
    git("add", "history.txt")
    git("commit", "-m", "Improve reading controls")
    assert get_commits_since_release(repository) == ["Improve reading controls"]

    untagged = tmp_path / "untagged"
    untagged.mkdir()
    subprocess.run(["git", "init"], cwd=untagged, check=True, capture_output=True, text=True)
    assert get_commits_since_release(untagged) == []


def test_local_metadata_boundaries_read_real_files_and_compare_values(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "fastlane" / "metadata"
    old_review = metadata / "review_info"
    current_review = metadata / "en-US" / "review_information"
    old_review.mkdir(parents=True)
    current_review.mkdir(parents=True)
    (old_review / "first_name.txt").write_text(" Old Name \n")
    (old_review / "phone_number.txt").write_text("+61 (02) 1234-5678")
    (current_review / "first_name.txt").write_text(" New Name ")
    (current_review / "phone_number.txt").write_text("0412 345 678")
    (current_review / "notes.txt").write_text("Use the supplied account")
    (current_review / "email_address.txt").write_text("  \n")

    assert read_text_fields(tmp_path / "absent", REVIEW_FIELDS) == {}
    assert read_text_fields(current_review, REVIEW_FIELDS, True) == {
        "contactFirstName": "New Name",
        "contactPhone": "+0412345678",
        "notes": "Use the supplied account",
    }
    defaults = {
        "contactFirstName": "Default",
        "contactLastName": "Reviewer",
        "contactPhone": "+100000000",
        "contactEmail": "default@example.com",
        "demoAccountRequired": False,
        "notes": "",
    }
    contact = review_contact(tmp_path, defaults)
    assert contact == {
        "contactFirstName": "New Name",
        "contactLastName": "Reviewer",
        "contactPhone": "+0412345678",
        "contactEmail": "default@example.com",
        "demoAccountRequired": False,
        "notes": "Use the supplied account",
    }
    assert defaults["contactFirstName"] == "Default"

    localized = metadata / "en-US"
    (localized / "description.txt").write_text("New description")
    (localized / "keywords.txt").write_text("cards,reading")
    (localized / "support_url.txt").write_text("https://example.com/support")
    (localized / "marketing_url.txt").write_text("   ")
    changes = metadata_updates(
        localized,
        VERSION_FIELDS,
        {
            "description": "Old description",
            "keywords": "cards,reading",
            "supportUrl": None,
        },
    )
    assert changes == {
        "description": "New description",
        "supportUrl": "https://example.com/support",
    }
    assert metadata_updates(tmp_path / "absent", APP_INFO_FIELDS, {}) == {}


def test_metadata_locale_selection_and_ipa_discovery_use_real_paths(
    tmp_path: Path,
) -> None:
    assert metadata_directories(tmp_path) == []
    primary = tmp_path / "fastlane" / "metadata" / "en-US"
    primary.mkdir(parents=True)
    assert metadata_directories(tmp_path) == [("en-US", primary), ("en-AU", primary)]
    secondary = tmp_path / "fastlane" / "metadata" / "en-AU"
    secondary.mkdir()
    assert metadata_directories(tmp_path) == [("en-US", primary), ("en-AU", secondary)]

    state = ProjectState()
    assert discover_ipa(tmp_path, state) is None
    export = tmp_path / "build" / "export"
    export.mkdir(parents=True)
    ipa = export / "Reader.ipa"
    ipa.write_bytes(b"signed archive")
    assert discover_ipa(tmp_path, state) == ipa
    assert state.metadata["ipa_path"] == str(ipa)
    configured = tmp_path / "Configured.ipa"
    configured.write_bytes(b"configured archive")
    state.metadata["ipa_path"] = str(configured)
    assert discover_ipa(tmp_path, state) == configured


def test_screenshot_parsing_grouping_and_actions_are_deterministic(
    tmp_path: Path,
) -> None:
    set_response = {
        "data": [
            {
                "id": "set-phone",
                "attributes": {"screenshotDisplayType": "APP_IPHONE_67"},
            },
            {
                "id": "set-tablet",
                "attributes": {"screenshotDisplayType": "APP_IPAD_PRO_3GEN_129"},
            },
        ]
    }
    screenshot_responses = {
        "set-phone": {
            "data": [
                {
                    "id": "shot-complete",
                    "attributes": {
                        "fileName": "iPhone 16 Pro Max-01.png",
                        "assetDeliveryState": {"state": "COMPLETE"},
                    },
                },
                {
                    "id": "shot-failed",
                    "attributes": {
                        "fileName": "iPhone 16 Pro Max-02.png",
                        "assetDeliveryState": {"state": "FAILED"},
                    },
                },
            ]
        },
        "set-tablet": None,
    }
    parsed = parse_screenshot_sets(set_response, screenshot_responses)
    assert parsed["APP_IPHONE_67"] == {
        "id": "set-phone",
        "screenshots": [
            {
                "id": "shot-complete",
                "filename": "iPhone 16 Pro Max-01.png",
                "state": "COMPLETE",
            },
            {
                "id": "shot-failed",
                "filename": "iPhone 16 Pro Max-02.png",
                "state": "FAILED",
            },
        ],
    }
    assert parsed["APP_IPAD_PRO_3GEN_129"] == {"id": "set-tablet", "screenshots": []}
    assert parse_screenshot_sets(None, {}) == {}

    names = [
        "iPhone 16 Pro Max-01.png",
        "iPhone-16-Plus-02.png",
        "iPad Pro 13-inch-01.png",
        "iPad-Pro-11-inch-01.png",
        "unrecognized.png",
    ]
    files = [tmp_path / name for name in names]
    grouped = group_screenshots(files)
    assert grouped["APP_IPHONE_67"] == files[:2]
    assert grouped["APP_IPAD_PRO_3GEN_129"] == [files[2]]
    assert grouped["APP_IPAD_PRO_3GEN_11"] == [files[3]]

    actions = screenshot_actions(files[:2], parsed["APP_IPHONE_67"])
    assert actions == {
        "set_id": "set-phone",
        "delete": ["shot-failed"],
        "upload": [files[1]],
        "complete": 1,
    }
    assert screenshot_actions(files, None, 2)["upload"] == files[:2]
    full = {
        "id": "set-full",
        "screenshots": [
            {"id": str(index), "filename": f"existing-{index}.png", "state": "COMPLETE"} for index in range(10)
        ],
    }
    assert screenshot_actions(files, full)["upload"] == []


def test_category_rating_content_and_price_planning() -> None:
    assert get_category_id("unused", "Games") == "GAMES"
    assert get_category_id("unused", "card games") == "GAMES_CARD"
    assert get_category_id("unused", "CUSTOM_CATEGORY") == "CUSTOM_CATEGORY"
    assert get_category_id("unused", "NEWID") == "NEWID"
    assert get_category_id("unused", "Unlisted") is None

    categories = categories_payload("info-1", "Games", "Card")
    assert categories["data"]["relationships"] == {
        "primaryCategory": {"data": {"type": "appCategories", "id": "GAMES"}},
        "secondaryCategory": {"data": {"type": "appCategories", "id": "GAMES_CARD"}},
    }
    assert categories_payload("info-1", "Games")["data"]["relationships"] == {
        "primaryCategory": {"data": {"type": "appCategories", "id": "GAMES"}}
    }
    assert categories_payload("info-1", "Unlisted") is None
    assert categories_payload("info-1", "Games", "Unlisted")["data"]["relationships"] == {
        "primaryCategory": {"data": {"type": "appCategories", "id": "GAMES"}}
    }

    rating = age_rating_attributes()
    assert rating["gambling"] is False
    assert rating["unrestrictedWebAccess"] is False
    assert rating["violenceRealistic"] == "NONE"
    assert len(rating) == 14

    assert content_rights_payload("app-1", False)["data"]["attributes"] == {
        "contentRightsDeclaration": "DOES_NOT_USE_THIRD_PARTY_CONTENT"
    }
    assert content_rights_payload("app-1", True)["data"]["attributes"] == {
        "contentRightsDeclaration": "USES_THIRD_PARTY_CONTENT"
    }

    price_link = f"{API_BASE_URL}/appPricePoints/price-1"
    assert (
        related_price_endpoint({"relationships": {"appPricePoint": {"links": {"related": price_link}}}})
        == "appPricePoints/price-1"
    )
    assert related_price_endpoint({}) is None
    points = {
        "data": [
            {"id": "price-1", "attributes": {"customerPrice": "3.99"}},
            {"id": "price-2", "attributes": {"customerPrice": "4.99"}},
        ]
    }
    assert find_price_point(points, "4.99") == "price-2"
    assert find_price_point(points, "9.99") is None
    assert find_price_point(None, "4.99") is None
    schedule = price_schedule_payload("app-1", "price-2")
    assert schedule["data"]["relationships"]["baseTerritory"]["data"]["id"] == "USA"
    assert schedule["included"][0]["relationships"]["appPricePoint"]["data"]["id"] == "price-2"


def test_local_normalization_defaults_and_altool_command(tmp_path: Path) -> None:
    assert generate_whats_new([], "Reader") == "Bug fixes and performance improvements."
    assert normalize_phone_number("+61 (02) 1234-5678") == "+610212345678"
    assert normalize_phone_number("0412 345 678") == "+0412345678"
    assert normalize_phone_number("") == ""
    ipa = tmp_path / "Reader.ipa"
    command = altool_command(ipa)
    assert command[:7] == [
        "xcrun",
        "altool",
        "--upload-app",
        "-f",
        str(ipa),
        "-t",
        "ios",
    ]
    assert command[-4] == "--apiKey"
    assert command[-2] == "--apiIssuer"
    assert main([]) == 1
    assert main([str(tmp_path)]) == 1
