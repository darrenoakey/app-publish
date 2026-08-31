import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import jwt

import modules.appstore as appstore_module
from config import API_KEY_ID
from modules.appstore import (
    check_app_exists_api,
    create_app_body,
    create_jwt_token,
    creation_instructions,
    get_headers,
    parse_apps_response,
    parse_fastlane_result,
    run,
)
from notices import RecordedNoticeStore
from session import IrisClient, ListedCookieSource
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


class IrisCreateHandler(BaseHTTPRequestHandler):
    status = 201
    created_id = "app-created"
    posts: list[bytes] = []

    def log_message(self, message_format: str, *args: object) -> None:
        del message_format, args

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.posts.append(self.rfile.read(length))
        body = json.dumps({"data": {"id": self.created_id}}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(handler) -> tuple[ThreadingHTTPServer, Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def request_local_apps(apps: list[dict], status: int = 200) -> tuple[bool, str | None]:
    LocalAppsHandler.apps = apps
    LocalAppsHandler.status = status
    server, thread = serve(LocalAppsHandler)
    try:
        return check_app_exists_api(
            "com.example.reader",
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_appstore_headers_contain_locally_signed_token() -> None:
    token = create_jwt_token()
    headers = get_headers()
    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["iss"]
    assert headers["Authorization"].startswith("Bearer ")
    assert API_KEY_ID
    assert headers["Content-Type"] == "application/json"


def test_appstore_validation_stops_before_external_actions(tmp_path, capsys) -> None:
    state = ProjectState()
    assert run(tmp_path, state) is False
    state.bundle_id = "com.example.reader"
    assert run(tmp_path, state) is False
    output = capsys.readouterr().out
    assert "No bundle ID" in output
    assert "No app name" in output

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


def test_run_creates_missing_app_via_iris_session(tmp_path) -> None:
    LocalAppsHandler.apps = []
    LocalAppsHandler.status = 200
    IrisCreateHandler.status = 201
    IrisCreateHandler.created_id = "app-created"
    IrisCreateHandler.posts = []
    jwt_server, jwt_thread = serve(LocalAppsHandler)
    iris_server, iris_thread = serve(IrisCreateHandler)
    try:
        iris = IrisClient(
            ListedCookieSource([{"name": "myacinfo", "value": "abc"}]),
            base_url=f"http://127.0.0.1:{iris_server.server_address[1]}/",
        )
        state = ProjectState(app_name="Reader", bundle_id="com.example.reader", current_version="1.0")
        notices = RecordedNoticeStore()
        ok = run(
            tmp_path,
            state,
            base_url=f"http://127.0.0.1:{jwt_server.server_address[1]}",
            iris=iris,
            notices=notices,
        )
    finally:
        jwt_server.shutdown()
        jwt_server.server_close()
        iris_server.shutdown()
        iris_server.server_close()
        jwt_thread.join()
        iris_thread.join()
    assert ok is True
    assert state.app_store_id == "app-created"
    assert json.loads(IrisCreateHandler.posts[0]) == create_app_body(
        "Reader", "com.example.reader", "com_example_reader", "1.0"
    )


def test_run_notifies_when_iris_create_is_forbidden(tmp_path) -> None:
    LocalAppsHandler.apps = []
    LocalAppsHandler.status = 200
    IrisCreateHandler.status = 403
    IrisCreateHandler.posts = []
    jwt_server, jwt_thread = serve(LocalAppsHandler)
    iris_server, iris_thread = serve(IrisCreateHandler)
    notices = RecordedNoticeStore()
    state = ProjectState(app_name="Reader", bundle_id="com.example.reader")
    try:
        iris = IrisClient(
            ListedCookieSource([{"name": "myacinfo", "value": "abc"}]),
            base_url=f"http://127.0.0.1:{iris_server.server_address[1]}/",
        )
        ok = run(
            tmp_path,
            state,
            base_url=f"http://127.0.0.1:{jwt_server.server_address[1]}",
            iris=iris,
            notices=notices,
        )
    finally:
        jwt_server.shutdown()
        jwt_server.server_close()
        iris_server.shutdown()
        iris_server.server_close()
        jwt_thread.join()
        iris_thread.join()
    assert ok is False
    assert notices.rows[0][0] == "app-publish"
    assert "Chrome session expired" in notices.rows[0][2]


def test_create_app_body_matches_spaceship_post_app() -> None:
    body = create_app_body("Reader", "com.example.reader", "com_example_reader", "1.0")
    assert body["data"]["attributes"]["bundleId"] == "com.example.reader"
    assert body["data"]["type"] == "apps"
    assert any(item["type"] == "appStoreVersions" for item in body["included"])
