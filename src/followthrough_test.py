import base64
import datetime as dt
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse

import pytest
import requests

from apple_portal import request as apple_request
from followthrough import (
    EXIT_ACTIONABLE,
    EXIT_BLOCKED,
    EXIT_QUIET,
    Finding,
    agent_link_call,
    asc_findings,
    collect_asc,
    load_state,
    mail_findings,
    run_followthrough,
)

NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)


def asc_document(app_id: str, version_state: str, submission_state: str, build_state: str) -> dict[str, dict]:
    return {
        "/apps": {"data": [{"id": app_id, "attributes": {"name": "Mira", "bundleId": "com.a.mira"}}]},
        f"/apps/{app_id}/appStoreVersions": {
            "data": [
                {
                    "id": f"v-{app_id}",
                    "attributes": {
                        "versionString": "3",
                        "appVersionState": version_state,
                        "createdDate": "2026-11-20T00:00:00Z",
                    },
                    "relationships": {"build": {"data": {"id": f"b-{app_id}", "type": "builds"}}},
                }
            ],
            "included": [
                {"id": f"b-{app_id}", "type": "builds", "attributes": {"version": "42", "processingState": "VALID"}}
            ],
        },
        "/reviewSubmissions": {
            "data": [
                {
                    "id": f"s-{app_id}",
                    "attributes": {"state": submission_state, "submittedDate": "2026-11-20T01:00:00Z"},
                }
            ]
        },
        "/builds": {
            "data": [
                {
                    "id": f"b2-{app_id}",
                    "attributes": {
                        "version": "43",
                        "processingState": build_state,
                        "uploadedDate": "2026-11-21T01:00:00Z",
                        "expired": False,
                    },
                    "relationships": {
                        "app": {"data": {"id": app_id}},
                        "preReleaseVersion": {"data": {"id": f"p-{app_id}"}},
                    },
                }
            ],
            "included": [{"id": f"p-{app_id}", "type": "preReleaseVersions", "attributes": {"version": "3"}}],
        },
    }


class LocalHandler(BaseHTTPRequestHandler):
    routes: dict[str, dict] = {}

    def log_message(self, message_format: str, *args: object) -> None:
        del message_format, args

    def _reply(self) -> None:
        path = urlparse(self.path).path
        if path not in self.routes:
            self.send_response(401)
            self.end_headers()
            return
        raw = json.dumps(self.routes[path]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = _reply
    do_POST = _reply


@pytest.fixture
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def local_asc(base: str):
    def request_fn(method: str, path: str, params=None, json=None) -> dict:
        del json
        response = requests.request(method, f"{base}/{path}", params=params, timeout=5)
        response.raise_for_status()
        return response.json()

    return request_fn


def local_mail(base: str):
    def call(function: str, body: dict) -> dict:
        response = requests.post(f"{base}/mail/{function}/{body.get('id', '')}".rstrip("/"), json=body, timeout=5)
        response.raise_for_status()
        return response.json()

    return call


def mail_routes(message_id: str, subject: str, body: str) -> dict[str, dict]:
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {
        "/mail/search": {"messages": [{"id": message_id, "threadId": message_id}]},
        f"/mail/get-message/{message_id}": {
            "internalDate": str(int(NOW.timestamp() * 1000)),
            "payload": {
                "headers": [{"name": "Subject", "value": subject}],
                "parts": [{"mimeType": "text/plain", "body": {"data": encoded}}],
            },
        },
    }


def test_asc_findings_classify_review_release_and_processing() -> None:
    snapshot = {
        "apps": [
            {
                "app_id": "1",
                "name": "Mira",
                "versions": [
                    {
                        "id": "v1",
                        "version": "2",
                        "state": "REJECTED",
                        "created": "a",
                        "build": "7",
                        "build_state": "VALID",
                    },
                    {
                        "id": "v0",
                        "version": "1",
                        "state": "READY_FOR_SALE",
                        "created": "0",
                        "build": "5",
                        "build_state": "VALID",
                    },
                ],
                "submissions": [
                    {"id": "s1", "state": "UNRESOLVED_ISSUES", "submittedDate": "2026-11-20T00:00:00Z"},
                    {"id": "s2", "state": "WAITING_FOR_REVIEW", "submittedDate": "2026-11-01T00:00:00Z"},
                    {"id": "s3", "state": "WAITING_FOR_REVIEW", "submittedDate": "2026-11-20T00:00:00Z"},
                    {"id": "s4", "state": "COMPLETE", "submittedDate": "2026-10-01T00:00:00Z"},
                ],
            },
            {
                "app_id": "2",
                "name": "Nox",
                "versions": [
                    {
                        "id": "v2",
                        "version": "4",
                        "state": "PENDING_DEVELOPER_RELEASE",
                        "created": "a",
                        "build": "9",
                        "build_state": "VALID",
                    }
                ],
                "submissions": [],
            },
        ],
        "builds": [
            {
                "id": "b1",
                "app_id": "1",
                "marketing": "2",
                "version": "8",
                "processingState": "INVALID",
                "uploadedDate": "2026-11-21T00:00:00Z",
                "expired": False,
            },
            {
                "id": "b2",
                "app_id": "1",
                "marketing": "2",
                "version": "9",
                "processingState": "PROCESSING",
                "uploadedDate": "2026-11-21T00:00:00Z",
                "expired": False,
            },
            {
                "id": "b3",
                "app_id": "2",
                "marketing": "4",
                "version": "10",
                "processingState": "PROCESSING",
                "uploadedDate": "2026-11-21T11:00:00Z",
                "expired": False,
            },
            {
                "id": "b4",
                "app_id": "2",
                "marketing": "4",
                "version": "11",
                "processingState": "FAILED",
                "uploadedDate": "2026-10-21T11:00:00Z",
                "expired": True,
            },
        ],
    }
    kinds = sorted((item.kind, item.ref) for item in asc_findings(snapshot, NOW))
    assert kinds == [
        ("awaiting_developer_release", "v2"),
        ("build_processing_failed", "b1"),
        ("build_processing_stalled", "b2"),
        ("review_rejected", "v1"),
        ("review_stalled", "s2"),
        ("review_unresolved", "s1"),
    ]


def test_mail_findings_name_app_and_keep_issue_text() -> None:
    mails = [
        {
            "id": "m1",
            "received": NOW.isoformat(),
            "subject": "You have a message from App Review about OL Nocturne (iOS).",
            "body": "",
        },
        {
            "id": "m2",
            "received": NOW.isoformat(),
            "subject": "The uploaded build for mcCompass has one or more issues.",
            "body": "ITMS-90683: Missing purpose string",
        },
        {
            "id": "m3",
            "received": NOW.isoformat(),
            "subject": 'The status of your (iOS) app, Mira, is now "Waiting for Review"',
            "body": "",
        },
    ]
    found = mail_findings(mails, {"OL Nocturne": "6829699356"})
    assert [(item.kind, item.app, item.app_id) for item in found] == [
        ("review_message_receipt", "OL Nocturne", "6829699356"),
        ("build_issue_receipt", "mcCompass", ""),
    ]
    assert "generic receipt" in found[0].detail
    assert "ITMS-90683" in found[1].detail


def test_run_reports_once_then_forgets_resolved_and_reports_recurrence(tmp_path, local_server) -> None:
    app_id = f"app-{uuid.uuid4()}"
    message_id = f"mail-{uuid.uuid4()}"
    state = tmp_path / "state.json"
    LocalHandler.routes = {
        **asc_document(app_id, "REJECTED", "UNRESOLVED_ISSUES", "VALID"),
        **mail_routes(message_id, "You have a message from App Review about Mira (iOS).", "Hello"),
    }
    asc, mail = local_asc(local_server), local_mail(local_server)
    code, lines = run_followthrough(state, now=NOW, request_fn=asc, mail_call=mail, evidence_path=tmp_path / "e.json")
    assert code == EXIT_ACTIONABLE
    assert sum(line.startswith("NEW ") for line in lines) == 3
    assert any(f"Mira ({app_id})" in line and "review_message_receipt" in line for line in lines)
    code, lines = run_followthrough(state, now=NOW + dt.timedelta(hours=6), request_fn=asc, mail_call=mail)
    assert code == EXIT_QUIET
    assert not any(line.startswith("NEW ") for line in lines)
    assert sum(line.startswith("KNOWN ") for line in lines) == 2
    LocalHandler.routes.update(asc_document(app_id, "READY_FOR_SALE", "COMPLETE", "VALID"))
    code, _ = run_followthrough(state, now=NOW + dt.timedelta(hours=12), request_fn=asc, mail_call=mail)
    assert code == EXIT_QUIET
    LocalHandler.routes.update(asc_document(app_id, "REJECTED", "COMPLETE", "INVALID"))
    code, lines = run_followthrough(state, now=NOW + dt.timedelta(hours=18), request_fn=asc, mail_call=mail)
    assert code == EXIT_ACTIONABLE
    assert sorted(line.split(":")[0] for line in lines if line.startswith("NEW ")) == [
        "NEW build_processing_failed",
        "NEW review_rejected",
    ]
    assert json.loads((tmp_path / "e.json").read_text())["snapshot"]["apps"][0]["app_id"] == app_id


def test_baseline_records_without_escalating(tmp_path, local_server) -> None:
    app_id = f"app-{uuid.uuid4()}"
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    state = tmp_path / "state.json"
    asc, mail = local_asc(local_server), local_mail(local_server)
    code, lines = run_followthrough(state, baseline=True, now=NOW, request_fn=asc, mail_call=mail)
    assert code == EXIT_QUIET
    assert any(line.startswith("BASELINE review_rejected") for line in lines)
    code, _ = run_followthrough(state, now=NOW + dt.timedelta(hours=6), request_fn=asc, mail_call=mail)
    assert code == EXIT_QUIET


def test_asc_outage_fails_closed_keeps_known_and_throttles(tmp_path, local_server) -> None:
    app_id = f"app-{uuid.uuid4()}"
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    state = tmp_path / "state.json"
    asc, mail = local_asc(local_server), local_mail(local_server)
    run_followthrough(state, baseline=True, now=NOW, request_fn=asc, mail_call=mail)
    LocalHandler.routes = {"/mail/search": {"messages": []}}
    code, lines = run_followthrough(state, now=NOW + dt.timedelta(hours=6), request_fn=asc, mail_call=mail)
    assert code == EXIT_BLOCKED
    assert any(line.startswith("BLOCKED asc-api") and "401" in line for line in lines)
    code, lines = run_followthrough(state, now=NOW + dt.timedelta(hours=12), request_fn=asc, mail_call=mail)
    assert code == EXIT_QUIET
    assert any("already escalated within 24h" in line for line in lines)
    code, _ = run_followthrough(state, now=NOW + dt.timedelta(hours=31), request_fn=asc, mail_call=mail)
    assert code == EXIT_BLOCKED
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    code, lines = run_followthrough(state, now=NOW + dt.timedelta(hours=36), request_fn=asc, mail_call=mail)
    assert code == EXIT_QUIET
    assert any(line.startswith("KNOWN review_rejected") for line in lines)
    assert load_state(state)["blockers"] == {}


def test_corrupt_state_fails_closed(tmp_path) -> None:
    state = tmp_path / "state.json"
    state.write_text("[]")
    with pytest.raises(ValueError):
        run_followthrough(state, now=NOW)
    assert state.read_text() == "[]"


def test_finding_fingerprint_is_stable() -> None:
    item = Finding("review_rejected", "1", "Mira", "2", "7", "REJECTED", "v1")
    assert item.fingerprint() == "review_rejected|1|2|7|REJECTED|v1"


def test_collect_asc_live_get_only_includes_groupiecam() -> None:
    snapshot = collect_asc(apple_request)
    assert "GroupieCam" in {app["name"] for app in snapshot["apps"]}
    assert snapshot["builds"]


def test_agent_link_live_apple_mail_search_is_reachable() -> None:
    result = agent_link_call("search", {"query": "from:no_reply@email.apple.com", "max_results": 1})
    assert isinstance(result.get("messages", []), list)
