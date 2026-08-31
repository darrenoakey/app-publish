import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from session import IrisClient, ListedCookieSource, SessionExpired, cookie_header


class IrisHandler(BaseHTTPRequestHandler):
    status = 201
    payload = {"data": {"id": "app-1"}}
    requests: list[tuple[str, str, dict, bytes]] = []

    def log_message(self, message_format: str, *args: object) -> None:
        del message_format, args

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.requests.append((self.command, self.path, dict(self.headers), body))
        raw = json.dumps(self.payload).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve() -> tuple[ThreadingHTTPServer, Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), IrisHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_cookie_header_joins_name_value_pairs() -> None:
    header = cookie_header([{"name": "myacinfo", "value": "abc"}, {"name": "itctx", "value": "xyz"}])
    assert header == "myacinfo=abc; itctx=xyz"


def test_iris_client_posts_session_cookies_to_local_server() -> None:
    IrisHandler.status = 201
    IrisHandler.payload = {"data": {"id": "app-1"}}
    IrisHandler.requests = []
    server, thread = serve()
    try:
        client = IrisClient(
            ListedCookieSource([{"name": "myacinfo", "value": "abc"}]),
            base_url=f"http://127.0.0.1:{server.server_address[1]}/",
        )
        document = client.request("POST", "v1/apps", json_body={"data": {"type": "apps"}})
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert document == {"data": {"id": "app-1"}}
    method, path, headers, body = IrisHandler.requests[0]
    lowered = {key.lower(): value for key, value in headers.items()}
    assert method == "POST"
    assert path == "/v1/apps"
    assert lowered.get("cookie") == "myacinfo=abc"
    assert lowered.get("x-csrf-itc") == "[asc-ui]"
    assert json.loads(body) == {"data": {"type": "apps"}}


def test_iris_client_raises_session_expired_on_forbidden() -> None:
    IrisHandler.status = 403
    IrisHandler.payload = {"errors": [{"title": "FORBIDDEN"}]}
    IrisHandler.requests = []
    server, thread = serve()
    try:
        client = IrisClient(
            ListedCookieSource([{"name": "myacinfo", "value": "abc"}]),
            base_url=f"http://127.0.0.1:{server.server_address[1]}/",
        )
        with pytest.raises(SessionExpired):
            client.request("POST", "v1/apps", json_body={"data": {"type": "apps"}})
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_iris_client_rejects_empty_cookies() -> None:
    client = IrisClient(ListedCookieSource([]), base_url="http://127.0.0.1:1/")
    with pytest.raises(SessionExpired):
        client.request("POST", "v1/apps")
