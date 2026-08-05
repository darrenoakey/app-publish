import importlib
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import jwt
import pytest

import modules.submit as submit_module
from config import API_KEY_ID
from modules.submit import (
    api_request,
    get_api_token,
    submit_for_review,
    tag_release,
    wait_for_build_processing,
)
from state import ProjectState

importlib.reload(submit_module)


class LocalReviewHandler(BaseHTTPRequestHandler):
    received: list[tuple[str, str]] = []

    def log_message(self, message_format: str, *args: object) -> None:
        del message_format, args

    def send_json(self, status: int, content: dict | None) -> None:
        body = json.dumps(content).encode() if content is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length)) if length else None

    def do_GET(self) -> None:
        self.received.append((self.command, self.path))
        if self.path.startswith("/apps?"):
            content = {"data": [{"id": "app-local"}]}
        elif "appStoreVersions?" in self.path:
            content = {
                "data": [
                    {
                        "id": "version-local",
                        "attributes": {
                            "versionString": "2.0",
                            "appStoreState": "PREPARE_FOR_SUBMISSION",
                        },
                    }
                ]
            }
        elif self.path.startswith("/appStoreVersions/"):
            content = {
                "data": {"attributes": {"appStoreState": "PREPARE_FOR_SUBMISSION"}},
                "included": [{"type": "builds", "id": "build-local"}],
            }
        elif self.path.startswith("/builds?"):
            content = {"data": [{"id": "processed-build-local"}]}
        elif self.path == "/error":
            self.send_json(422, {"message": "local rejection"})
            return
        else:
            content = {"data": []}
        self.send_json(200, content)

    def do_POST(self) -> None:
        self.received.append((self.command, self.path))
        self.read_json()
        resource_id = "submission-local" if self.path == "/reviewSubmissions" else "item-local"
        self.send_json(201, {"data": {"id": resource_id}})

    def do_PATCH(self) -> None:
        self.received.append((self.command, self.path))
        self.send_json(200, {"data": self.read_json()})

    def do_DELETE(self) -> None:
        self.received.append((self.command, self.path))
        self.send_json(204, None)


def run_local_review(action):
    LocalReviewHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalReviewHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    host, port = server.server_address
    try:
        return action(f"http://{host}:{port}"), list(LocalReviewHandler.received)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_api_token_is_locally_signed_with_configured_key() -> None:
    token = get_api_token()
    header = jwt.get_unverified_header(token)
    payload = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    assert header["alg"] == "ES256"
    assert header["kid"] == API_KEY_ID
    assert payload["aud"] == "appstoreconnect-v1"
    assert 0 < payload["exp"] - payload["iat"] <= 1201


def test_submit_transport_and_review_flow_use_a_real_local_service(tmp_path) -> None:
    token = get_api_token()

    def exercise(base_url: str) -> bool:
        assert api_request("GET", "error", token, base_url=base_url) is None
        with pytest.raises(ValueError, match="Unknown method: TRACE"):
            api_request("TRACE", "items", token, base_url=base_url)
        state = ProjectState(bundle_id="com.example.reader", current_version="2.0")
        assert (
            wait_for_build_processing(
                ProjectState(bundle_id="com.example.reader", current_build=7),
                max_wait_minutes=2,
                base_url=base_url,
            )
            is True
        )
        return submit_for_review(tmp_path, state, base_url)

    result, received = run_local_review(exercise)
    assert result is True
    assert ("POST", "/reviewSubmissions") in received
    assert ("POST", "/reviewSubmissionItems") in received
    assert ("PATCH", "/reviewSubmissions/submission-local") in received


def test_release_tag_is_created_in_a_real_local_git_repository(tmp_path) -> None:
    repository = tmp_path / "repository"
    remote = tmp_path / "remote.git"
    repository.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Submit Test"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "submit@example.com"],
        cwd=repository,
        check=True,
    )
    (repository / "README.md").write_text("Reader\n")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial release"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repository, check=True)
    state = ProjectState(current_version="2.0", current_build=7)
    assert tag_release(repository, state) is True
    assert tag_release(repository, state) is True
    tags = subprocess.run(
        ["git", "tag"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert tags == ["v2.0"]
