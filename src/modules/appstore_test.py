import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import jwt

import modules.appstore as appstore_module
from config import API_KEY_ID
from modules.appstore import (
    check_app_exists_api,
    create_jwt_token,
    creation_instructions,
    ensure_create_app_lane,
    get_headers,
    open_app_store_connect_and_show_instructions,
    parse_apps_response,
    parse_fastlane_result,
    run,
)
from state import ProjectState

importlib.reload(appstore_module)


class LocalAppsHandler(BaseHTTPRequestHandler):
    status = 200
    apps: list[dict] = []

    def log_message(self, message_format: str, *args: object) -> None:
        del message_format, args

    def do_GET(self) -> None:
        body = json.dumps({"data": self.apps}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def request_local_apps(apps: list[dict], status: int = 200) -> tuple[bool, str | None]:
    LocalAppsHandler.apps = apps
    LocalAppsHandler.status = status
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAppsHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    host, port = server.server_address
    try:
        return check_app_exists_api("com.example.reader", f"http://{host}:{port}/")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_appstore_headers_contain_locally_signed_token() -> None:
    token = create_jwt_token()
    assert token
    header = jwt.get_unverified_header(token)
    payload = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    assert header["kid"] == API_KEY_ID
    assert payload["aud"] == "appstoreconnect-v1"
    headers = get_headers()
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["Content-Type"] == "application/json"


def test_create_app_lane_is_written_once_to_a_real_fastfile(tmp_path) -> None:
    state = ProjectState(bundle_id="com.example.reader", app_name="Reader")
    assert ensure_create_app_lane(tmp_path, state) is False
    fastlane = tmp_path / "fastlane"
    fastlane.mkdir()
    fastfile = fastlane / "Fastfile"
    fastfile.write_text("platform :ios do\nend\n")
    assert ensure_create_app_lane(tmp_path, state) is True
    content = fastfile.read_text()
    assert "lane :create_app" in content
    assert 'bundle_id = "com.example.reader"' in content
    assert 'app_name = "Reader"' in content
    assert ensure_create_app_lane(tmp_path, state) is True
    assert fastfile.read_text() == content

    without_end = tmp_path / "without-end"
    (without_end / "fastlane").mkdir(parents=True)
    second_fastfile = without_end / "fastlane" / "Fastfile"
    second_fastfile.write_text("platform :ios\n")
    assert ensure_create_app_lane(without_end, state) is True
    assert "lane :create_app" in second_fastfile.read_text()


def test_appstore_validation_stops_before_external_actions(tmp_path, capsys) -> None:
    state = ProjectState()
    assert run(tmp_path, state) is False
    state.bundle_id = "com.example.reader"
    assert run(tmp_path, state) is False
    open_app_store_connect_and_show_instructions(ProjectState())
    open_app_store_connect_and_show_instructions(ProjectState(app_name="Reader"))
    output = capsys.readouterr().out
    assert "No bundle ID" in output
    assert "No app name" in output
    assert "app_name not set" in output
    assert "bundle_id not set" in output

    lines = creation_instructions(ProjectState(app_name="Reader", bundle_id="com.example.reader"))
    assert "CREATE NEW APP IN APP STORE CONNECT" in lines
    assert "   Name:               Reader" in lines
    assert "   Bundle ID:          com.example.reader" in lines
    assert "   SKU:                com_example_reader" in lines

    assert parse_apps_response({"data": []}) == (False, None)
    assert parse_apps_response({"data": [{"id": "app-1"}]}) == (True, "app-1")
    assert parse_fastlane_result("unrelated output") == (False, None)
    assert parse_fastlane_result("APP_STORE_ID=app-1\nAPP_EXISTS=true\n") == (
        True,
        "app-1",
    )


def test_appstore_api_boundary_uses_a_real_local_http_service() -> None:
    assert request_local_apps([{"id": "app-local"}]) == (True, "app-local")
    assert request_local_apps([]) == (False, None)
    assert request_local_apps([], 503) == (None, None)
