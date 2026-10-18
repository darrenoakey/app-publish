import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import psycopg2

from notices import PostgresNoticeStore, TEST_NOTICE_DSN
from session import IrisClient, ListedCookieSource
from state import ProjectState

from modules.privacy import not_collected_body, publish_not_collected, publish_state_body, run


class PrivacyHandler(BaseHTTPRequestHandler):
    status = 201
    requests: list[tuple[str, bytes]] = []
    existing_usages = False

    def log_message(self, message_format: str, *args: object) -> None:
        del message_format, args

    def do_GET(self) -> None:
        self.requests.append((self.path, b""))
        raw = b'{"data":[{"id":"usage-0"}]}' if self.existing_usages else b'{"data":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_PATCH(self) -> None:
        self.do_POST()

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
    paths = [path for path, _ in PrivacyHandler.requests]
    assert paths == [
        "/v1/apps/1523241431/dataUsages?limit=1",
        "/v1/appDataUsages",
        "/v1/appDataUsagesPublishState/1523241431",
    ]
    assert json.loads(PrivacyHandler.requests[1][1]) == not_collected_body("1523241431")
    assert json.loads(PrivacyHandler.requests[2][1]) == publish_state_body("1523241431")


def test_publish_not_collected_skips_duplicate_usage_but_still_publishes() -> None:
    PrivacyHandler.status = 200
    PrivacyHandler.requests = []
    PrivacyHandler.existing_usages = True
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
        PrivacyHandler.existing_usages = False
        server.shutdown()
        server.server_close()
        thread.join()
    assert [path for path, _ in PrivacyHandler.requests] == [
        "/v1/apps/1523241431/dataUsages?limit=1",
        "/v1/appDataUsagesPublishState/1523241431",
    ]


def test_run_notifies_when_chrome_session_expired(tmp_path: Path) -> None:
    PrivacyHandler.status = 403
    PrivacyHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), PrivacyHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app_store_id = f"privacy-{uuid.uuid4()}"
    key = f"session-privacy:{app_store_id}"
    notices = PostgresNoticeStore(TEST_NOTICE_DSN)
    state = ProjectState(app_name="OL Golf", bundle_id="com.darrenoakey.olGolf", app_store_id=app_store_id)
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
    with psycopg2.connect(TEST_NOTICE_DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT source, message FROM waggler_notifications WHERE idempotency_key = %s",
                (key,),
            )
            rows = cursor.fetchall()
            cursor.execute("DELETE FROM waggler_notifications WHERE idempotency_key = %s", (key,))
        connection.commit()
    assert rows[0][0] == "app-publish"
    assert "DATA_NOT_COLLECTED" in rows[0][1]
