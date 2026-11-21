import base64
import datetime as dt
import json
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from apple_portal import request as apple_request
from followthrough import (
    EXIT_ACTIONABLE,
    EXIT_BLOCKED,
    EXIT_QUIET,
    BoardClient,
    DeliveryError,
    Finding,
    acknowledge,
    agent_link_call,
    asc_findings,
    collect_asc,
    deliver,
    delivery_marker,
    load_state,
    mail_findings,
    run_followthrough,
)

NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)
SCRIPT = Path(__file__).parent / "followthrough.py"
SENDER = "conv-followthrough-test-sender"


def asc_document(app_id: str, version_state: str, submission_state: str, build_state: str) -> dict[str, dict]:
    return {
        "/apps": {
            "data": [{"id": app_id, "attributes": {"name": "Mira", "bundleId": "com.a.mira"}}],
            "meta": {"paging": {"total": 1, "limit": 200}},
        },
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
            "meta": {"paging": {"total": 1, "limit": 10}},
        },
        "/reviewSubmissions": {
            "data": [
                {
                    "id": f"s-{app_id}",
                    "attributes": {"state": submission_state, "submittedDate": "2026-11-20T01:00:00Z"},
                }
            ],
            "meta": {"paging": {"total": 1, "limit": 20}},
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
            "meta": {"paging": {"total": 1, "limit": 200}},
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


def serve(handler: type[BaseHTTPRequestHandler]):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def local_server():
    server, thread, base = serve(LocalHandler)
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


# ##################################################################
# owner sink
# isolated stand-in for the AgentD3 board API (POST /v1/board -> 202, GET
# /v1/board newest-first where after=N means id < N) with injectable failures
class OwnerSink:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.messages: list[dict] = []
        self.next_id = 1000
        self.fail_next_admissions = 0
        self.drop_next_after_store = 0
        self.posts = 0
        self.reads = 0

    def add(self, sender: str, target: str, text: str) -> dict:
        with self.lock:
            self.next_id += 1
            message = {
                "id": self.next_id,
                "kind": "direct",
                "sender_id": sender,
                "target": target,
                "text": text,
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
            }
            self.messages.append(message)
            return message

    def with_marker(self, marker: str) -> list[dict]:
        return [message for message in self.messages if marker in message["text"]]


def sink_handler(sink: OwnerSink) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, message_format: str, *args: object) -> None:
            del message_format, args

        def _json(self, status: int, payload: dict) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            sink.posts += 1
            if sink.fail_next_admissions:
                sink.fail_next_admissions -= 1
                self._json(503, {"error": "owner admission unavailable"})
                return
            if body.get("kind") != "direct" or not body.get("target_id"):
                self._json(400, {"error": "direct target_id is required"})
                return
            message = sink.add(body.get("sender_id", ""), body["target_id"], body["body"])
            if sink.drop_next_after_store:
                sink.drop_next_after_store -= 1
                self.close_connection = True
                return
            self._json(202, {"message": message, "superseded": False})

        def do_GET(self) -> None:
            sink.reads += 1
            query = {key: values[0] for key, values in parse_qs(urlparse(self.path).query).items()}
            limit = int(query.get("limit", 50))
            after = int(query.get("after", 0))
            with sink.lock:
                rows = [m for m in sink.messages if m["kind"] == query.get("kind", m["kind"])]
                rows = [m for m in rows if not after or m["id"] < after]
                rows = sorted(rows, key=lambda m: m["id"], reverse=True)[:limit]
            self._json(200, {"messages": rows, "next_after": rows[-1]["id"] if rows else after})

    return Handler


@pytest.fixture
def owner_sink():
    sink = OwnerSink()
    server, thread, base = serve(sink_handler(sink))
    try:
        yield sink, base
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


def ids_from(lines: list[str], label: str) -> list[str]:
    return [line.split()[1].removeprefix("id=") for line in lines if line.startswith(label + " id=")]


def cli_deliver(state: Path, board: str, target: str, *ids: str) -> subprocess.CompletedProcess:
    argv = [
        sys.executable,
        str(SCRIPT),
        "--state",
        str(state),
        "--board-url",
        board,
        "--to",
        target,
        "--sender",
        SENDER,
    ]
    for item in ids:
        argv += ["--deliver", item]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120)


def scan(state: Path, base: str, hours: float = 0, **kwargs):
    return run_followthrough(
        state, now=NOW + dt.timedelta(hours=hours), request_fn=local_asc(base), mail_call=local_mail(base), **kwargs
    )


def rejected_with_mail(app_id: str, message_id: str) -> dict[str, dict]:
    return {
        **asc_document(app_id, "REJECTED", "UNRESOLVED_ISSUES", "VALID"),
        **mail_routes(message_id, "You have a message from App Review about Mira (iOS).", "Hello"),
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


def test_dropped_continuation_keeps_pending_across_restarts_then_delivers_once(
    tmp_path, local_server, owner_sink
) -> None:
    sink, board = owner_sink
    app_id, owner = f"app-{uuid.uuid4()}", f"conv-owner-{uuid.uuid4().hex[:8]}"
    state = tmp_path / "state.json"
    LocalHandler.routes = rejected_with_mail(app_id, f"mail-{uuid.uuid4()}")
    code, lines = scan(state, local_server, evidence_path=tmp_path / "e.json")
    assert code == EXIT_ACTIONABLE
    first = ids_from(lines, "NEW")
    assert len(first) == 3
    assert any(line.startswith("DELIVER each pending id once") for line in lines)
    # the continuation never ran (escalation failed / was interrupted): every later scan still escalates
    for hours in (6, 12):
        code, lines = scan(state, local_server, hours=hours)
        assert code == EXIT_ACTIONABLE
        assert ids_from(lines, "NEW") == []
        assert sorted(ids_from(lines, "PENDING")) == sorted(first)
    done = cli_deliver(state, board, owner, *first)
    assert done.returncode == 0, done.stderr
    assert done.stdout.count("(posted)") == 3
    for hours in (18, 24):
        code, lines = scan(state, local_server, hours=hours)
        assert code == EXIT_QUIET
        assert ids_from(lines, "PENDING") == []
        assert sum(line.startswith("KNOWN ") for line in lines) == 2
    replay = cli_deliver(state, board, owner, *first)
    assert replay.returncode == 0, replay.stderr
    assert replay.stdout.count("ALREADY DELIVERED") == 3
    assert sink.posts == 1
    assert all(len(sink.with_marker(marker)) == 1 for marker in first)
    assert json.loads((tmp_path / "e.json").read_text())["snapshot"]["apps"][0]["app_id"] == app_id


def test_failed_first_admission_keeps_pending_and_retry_delivers_once(tmp_path, local_server, owner_sink) -> None:
    sink, board = owner_sink
    app_id, owner = f"app-{uuid.uuid4()}", f"conv-owner-{uuid.uuid4().hex[:8]}"
    state = tmp_path / "state.json"
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    _, lines = scan(state, local_server)
    [marker] = ids_from(lines, "NEW")
    sink.fail_next_admissions = 1
    failed = cli_deliver(state, board, owner, marker)
    assert failed.returncode == EXIT_BLOCKED
    assert "503" in failed.stderr
    assert sink.messages == []
    assert load_state(state)["sending"]
    code, lines = scan(state, local_server, hours=6)
    assert code == EXIT_ACTIONABLE
    assert ids_from(lines, "PENDING") == [marker]
    retried = cli_deliver(state, board, owner, marker)
    assert retried.returncode == 0, retried.stderr
    assert "(posted)" in retried.stdout
    assert len(sink.with_marker(marker)) == 1
    stored = load_state(state)
    assert stored["pending"] == {} and stored["sending"] == {}
    assert next(iter(stored["delivered"].values()))["message_id"] == str(sink.with_marker(marker)[0]["id"])


def test_interrupted_after_admission_recovers_from_board_without_resend(tmp_path, local_server, owner_sink) -> None:
    sink, board = owner_sink
    app_id, owner = f"app-{uuid.uuid4()}", f"conv-owner-{uuid.uuid4().hex[:8]}"
    state = tmp_path / "state.json"
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    _, lines = scan(state, local_server)
    [marker] = ids_from(lines, "NEW")
    sink.drop_next_after_store = 1
    interrupted = cli_deliver(state, board, owner, marker)
    assert interrupted.returncode == EXIT_BLOCKED
    assert "ConnectionError" in interrupted.stderr
    [stored] = sink.with_marker(marker)
    for index in range(450):  # newer traffic forces the recovery search across pages
        sink.add("conv-noise", owner if index % 2 else "conv-other", f"noise {index}")
    code, lines = scan(state, local_server, hours=6)
    assert code == EXIT_ACTIONABLE
    assert ids_from(lines, "PENDING") == [marker]
    recovered = cli_deliver(state, board, owner, marker)
    assert recovered.returncode == 0, recovered.stderr
    assert f"board message {stored['id']} (recovered)" in recovered.stdout
    posts = sink.posts
    for _ in range(3):  # repeated replays and repeated reads never post again
        replay = cli_deliver(state, board, owner, marker)
        assert "ALREADY DELIVERED" in replay.stdout
        code, _ = scan(state, local_server, hours=12)
        assert code == EXIT_QUIET
    assert sink.posts == posts
    assert len(sink.with_marker(marker)) == 1
    assert sink.reads >= 3


def test_concurrent_deliveries_post_once(tmp_path, local_server, owner_sink) -> None:
    sink, board = owner_sink
    app_id, owner = f"app-{uuid.uuid4()}", f"conv-owner-{uuid.uuid4().hex[:8]}"
    state = tmp_path / "state.json"
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    _, lines = scan(state, local_server)
    [marker] = ids_from(lines, "NEW")
    argv = [
        sys.executable,
        str(SCRIPT),
        "--state",
        str(state),
        "--board-url",
        board,
        "--to",
        owner,
        "--sender",
        SENDER,
        "--deliver",
        marker,
    ]
    runs = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outputs = [run.communicate(timeout=120) for run in runs]
    assert all(run.returncode == 0 for run in runs), outputs
    joined = "".join(out for out, _ in outputs)
    assert joined.count("DELIVERED id=") - joined.count("ALREADY DELIVERED") == 1
    assert joined.count("ALREADY DELIVERED") == 1
    assert len(sink.with_marker(marker)) == 1


def test_intent_cannot_switch_owner(tmp_path, local_server, owner_sink) -> None:
    sink, board = owner_sink
    app_id = f"app-{uuid.uuid4()}"
    state = tmp_path / "state.json"
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    _, lines = scan(state, local_server)
    [marker] = ids_from(lines, "NEW")
    sink.fail_next_admissions = 1
    with pytest.raises(DeliveryError):
        deliver(state, [marker], "conv-owner-a", SENDER, board=BoardClient(board))
    with pytest.raises(DeliveryError, match="refusing a second owner"):
        deliver(state, [marker], "conv-owner-b", SENDER, board=BoardClient(board))
    with pytest.raises(DeliveryError, match="open delivery intent"):
        acknowledge(state, [marker], "out of scope")
    assert sink.messages == []


def test_recurrence_after_resolution_is_a_new_delivery(tmp_path, local_server, owner_sink) -> None:
    sink, board = owner_sink
    app_id, owner = f"app-{uuid.uuid4()}", f"conv-owner-{uuid.uuid4().hex[:8]}"
    state = tmp_path / "state.json"
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    _, lines = scan(state, local_server)
    [first] = ids_from(lines, "NEW")
    deliver(state, [first], owner, SENDER, board=BoardClient(board))
    LocalHandler.routes.update(asc_document(app_id, "READY_FOR_SALE", "COMPLETE", "VALID"))
    assert scan(state, local_server, hours=6)[0] == EXIT_QUIET
    LocalHandler.routes.update(asc_document(app_id, "REJECTED", "COMPLETE", "INVALID"))
    code, lines = scan(state, local_server, hours=12)
    assert code == EXIT_ACTIONABLE
    again = ids_from(lines, "NEW")
    assert len(again) == 2 and first not in again
    deliver(state, again, owner, SENDER, board=BoardClient(board))
    assert len(sink.messages) == 2
    assert all(len(sink.with_marker(marker)) == 1 for marker in [first, *again])


def test_ack_records_out_of_scope_without_a_message(tmp_path, local_server, owner_sink) -> None:
    sink, _ = owner_sink
    app_id = f"app-{uuid.uuid4()}"
    state = tmp_path / "state.json"
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    _, lines = scan(state, local_server)
    [marker] = ids_from(lines, "NEW")
    code, out = acknowledge(state, [marker], "OL Videos is out of scope")
    assert code == EXIT_QUIET and out[0].startswith(f"ACKNOWLEDGED id={marker}")
    assert scan(state, local_server, hours=6)[0] == EXIT_QUIET
    assert acknowledge(state, [marker], "again")[1][0].startswith("ALREADY DELIVERED")
    with pytest.raises(DeliveryError, match="unknown"):
        acknowledge(state, ["asc-ft-0000000000000000"], "nope")
    assert sink.messages == []


def test_schema1_state_migrates_with_reconciled_fingerprints_preserved(tmp_path, local_server) -> None:
    app_id = f"app-{uuid.uuid4()}"
    state = tmp_path / "state.json"
    LocalHandler.routes = {
        **asc_document(app_id, "REJECTED", "UNRESOLVED_ISSUES", "VALID"),
        "/mail/search": {"messages": []},
    }
    current = [
        f"review_rejected|{app_id}|3|42|REJECTED|v-{app_id}",
        f"review_unresolved|{app_id}|3|42|UNRESOLVED_ISSUES|s-{app_id}",
    ]
    mail = [f"review_message_receipt|{app_id}|||2026-11-21T0{n}:00:00+00:00|gmail:m{n}" for n in range(5)]
    seen = {fingerprint: "2026-11-21T10:05:34+00:00" for fingerprint in current + mail}
    state.write_text(
        json.dumps(
            {
                "schema": 1,
                "seen": seen,
                "mail_seen": {f"m{n}": "2026-11-21T0{n}:00:00+00:00".format(n=n) for n in range(5)},
                "blockers": {},
                "mail_cursor": "2026-11-21T10:05:34+00:00",
                "last_run": {"at": "2026-11-21T10:05:34+00:00"},
            }
        )
    )
    migrated = load_state(state)
    assert migrated["schema"] == 2 and migrated["pending"] == {} and migrated["sending"] == {}
    assert set(migrated["delivered"]) == set(seen)
    assert {entry["disposition"] for entry in migrated["delivered"].values()} == {"schema1-reported"}
    code, lines = scan(state, local_server, hours=1)
    assert code == EXIT_QUIET
    assert sum(line.startswith("KNOWN ") for line in lines) == 2
    after = load_state(state)
    assert set(seen) <= set(after["seen"]) and set(seen) <= set(after["delivered"])
    assert after["pending"] == {}


def test_baseline_records_without_escalating(tmp_path, local_server) -> None:
    app_id = f"app-{uuid.uuid4()}"
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    state = tmp_path / "state.json"
    code, lines = scan(state, local_server, baseline=True)
    assert code == EXIT_QUIET
    assert any(line.startswith("BASELINE review_rejected") for line in lines)
    assert load_state(state)["pending"] == {}
    code, _ = scan(state, local_server, hours=6)
    assert code == EXIT_QUIET


def test_asc_outage_fails_closed_keeps_known_and_throttles(tmp_path, local_server) -> None:
    app_id = f"app-{uuid.uuid4()}"
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    state = tmp_path / "state.json"
    scan(state, local_server, baseline=True)
    LocalHandler.routes = {"/mail/search": {"messages": []}}
    code, lines = scan(state, local_server, hours=6)
    assert code == EXIT_BLOCKED
    assert any(line.startswith("BLOCKED asc-api") and "401" in line for line in lines)
    code, lines = scan(state, local_server, hours=12)
    assert code == EXIT_QUIET
    assert any("already escalated within 24h" in line for line in lines)
    code, _ = scan(state, local_server, hours=31)
    assert code == EXIT_BLOCKED
    LocalHandler.routes = {**asc_document(app_id, "REJECTED", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    code, lines = scan(state, local_server, hours=36)
    assert code == EXIT_QUIET
    assert any(line.startswith("KNOWN review_rejected") for line in lines)
    assert load_state(state)["blockers"] == {}


def test_coverage_gaps_are_reported_escalated_once_and_cleared(tmp_path, local_server) -> None:
    app_id = f"app-{uuid.uuid4()}"
    routes = {**asc_document(app_id, "READY_FOR_SALE", "COMPLETE", "VALID"), "/mail/search": {"messages": []}}
    routes[f"/apps/{app_id}/appStoreVersions"]["meta"] = {"paging": {"total": 14, "limit": 10}}
    routes["/mail/search"] = {"messages": [], "nextPageToken": "more"}
    LocalHandler.routes = routes
    state = tmp_path / "state.json"
    code, lines = scan(state, local_server)
    assert code == EXIT_BLOCKED
    gaps = [line for line in lines if line.startswith("COVERAGE GAP (new)")]
    assert len(gaps) == 2
    assert any(f"appStoreVersions:Mira ({app_id}): first page returned 1 of 14" in line for line in gaps)
    assert any("apple-mail" in line for line in gaps)
    assert load_state(state)["mail_cursor"] is None
    assert any(line.startswith("coverage: ") and "(1 truncated)" in line for line in lines)
    code, lines = scan(state, local_server, hours=6)
    assert code == EXIT_QUIET
    assert sum(line.startswith("COVERAGE GAP (known since") for line in lines) == 2
    LocalHandler.routes = {
        **asc_document(app_id, "READY_FOR_SALE", "COMPLETE", "VALID"),
        "/mail/search": {"messages": []},
    }
    code, lines = scan(state, local_server, hours=12)
    assert code == EXIT_QUIET
    assert not any(line.startswith("COVERAGE GAP") for line in lines)
    stored = load_state(state)
    assert stored["coverage_gaps"] == {} and stored["mail_cursor"] is not None


def test_corrupt_state_fails_closed(tmp_path) -> None:
    state = tmp_path / "state.json"
    state.write_text("[]")
    with pytest.raises(ValueError):
        run_followthrough(state, now=NOW)
    assert state.read_text() == "[]"
    state.write_text(json.dumps({"schema": 2, "seen": {}, "mail_seen": {}, "blockers": {}}))
    with pytest.raises(ValueError, match="pending"):
        load_state(state)


def test_finding_fingerprint_and_marker_are_stable() -> None:
    item = Finding("review_rejected", "1", "Mira", "2", "7", "REJECTED", "v1")
    assert item.fingerprint() == "review_rejected|1|2|7|REJECTED|v1"
    assert delivery_marker(item.fingerprint(), "a") == delivery_marker(item.fingerprint(), "a")
    assert delivery_marker(item.fingerprint(), "a") != delivery_marker(item.fingerprint(), "b")


def test_collect_asc_live_get_only_includes_groupiecam() -> None:
    snapshot = collect_asc(apple_request)
    assert "GroupieCam" in {app["name"] for app in snapshot["apps"]}
    assert snapshot["builds"]
    assert {item["source"] for item in snapshot["coverage"]} >= {"apps", "builds"}


def test_agent_link_live_apple_mail_search_is_reachable() -> None:
    result = agent_link_call("search", {"query": "from:no_reply@email.apple.com", "max_results": 1})
    assert isinstance(result.get("messages", []), list)
