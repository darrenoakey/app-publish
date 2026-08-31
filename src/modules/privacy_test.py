import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from notices import RecordedNoticeStore
from session import IrisClient, ListedCookieSource
from state import ProjectState

from modules.privacy import not_collected_body, publish_not_collected, run


class PrivacyHandler(BaseHTTPRequestHandler):
    status = 201
    requests: list[tuple[str, bytes]] = []

    def log_message(self, message_format: str, *args: object) -> None:
        del message_format, args

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.requests.append((self.path, body))
        raw = b'{"data":{"id":"usage-1"}}'
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_not_collected_body_uses_data_not_collected() -> None:
    body = not_collected_body("1523241431")
    protection = body["data"]["relationships"]["dataProtection"]["data"]
    assert protection == {"type": "appDataUsageDataProtections", "id": "DATA_NOT_COLLECTED"}
    assert body["data"]["relationships"]["app"]["data"]["id"] == "1523241431"


def test_publish_not_collected_posts_iris_payload() -> None:
    PrivacyHandler.status = 201
    PrivacyHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), PrivacyHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        iris = IrisClient(
            ListedCookieSource([{"name": "myacinfo", "value": "abc"}]),
            base_url=f"http://127.0.0.1:{server.server_address[1]}/",
        )
        publish_not_collected(iris, "1523241431")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    path, body = PrivacyHandler.requests[0]
    assert path == "/v1/appDataUsages"
    assert json.loads(body) == not_collected_body("1523241431")


def test_run_notifies_when_chrome_session_expired(tmp_path: Path) -> None:
    PrivacyHandler.status = 403
    PrivacyHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), PrivacyHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    notices = RecordedNoticeStore()
    state = ProjectState(app_name="OL Golf", bundle_id="com.darrenoakey.olGolf", app_store_id="1")
    try:
        iris = IrisClient(
            ListedCookieSource([{"name": "myacinfo", "value": "abc"}]),
            base_url=f"http://127.0.0.1:{server.server_address[1]}/",
        )
        assert run(tmp_path, state, iris=iris, notices=notices) is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert notices.rows[0][0] == "app-publish"
    assert "DATA_NOT_COLLECTED" in notices.rows[0][2]
