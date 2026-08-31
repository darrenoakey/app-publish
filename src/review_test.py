import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import requests

from apple_portal import request as apple_request
from notices import RecordedNoticeStore
from review import (
    ReviewVersion,
    collect_review_status,
    format_notice,
    new_bad_states,
    print_reviews,
    snapshot_from,
    watch_reviews,
)


REJECTED_DOCUMENT = {
    "data": [
        {
            "id": "1",
            "attributes": {"name": "OL Golf", "bundleId": "com.darrenoakey.olGolf"},
            "relationships": {"appStoreVersions": {"data": [{"id": "v1", "type": "appStoreVersions"}]}},
        }
    ],
    "included": [
        {
            "id": "v1",
            "type": "appStoreVersions",
            "attributes": {"versionString": "1.0.2", "appVersionState": "REJECTED"},
        }
    ],
}


class AppsHandler(BaseHTTPRequestHandler):
    document = REJECTED_DOCUMENT

    def log_message(self, message_format: str, *args: object) -> None:
        del message_format, args

    def do_GET(self) -> None:
        raw = json.dumps(self.document).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def local_request(base: str):
    def request_fn(method: str, path: str, params=None, json=None) -> dict:
        del json
        response = requests.request(method, f"{base}/{path}", params=params, timeout=5)
        response.raise_for_status()
        return response.json()

    return request_fn


def test_collect_and_watch_rejected_transition_against_local_asc(tmp_path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), AppsHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        rows = collect_review_status(local_request(base))
        assert rows == [ReviewVersion("1", "OL Golf", "com.darrenoakey.olGolf", "1.0.2", "REJECTED", "v1")]
        notices = RecordedNoticeStore()
        snapshot = tmp_path / "review-status.json"
        first = watch_reviews(
            snapshot,
            local_request(base),
            notices,
            lookup=lambda name: "ITMS-90111: Unsupported SDK",
        )
        assert len(first) == 1
        assert "ITMS-90111" in notices.rows[0][2]
        second = watch_reviews(
            snapshot,
            local_request(base),
            notices,
            lookup=lambda name: "ITMS-90111: Unsupported SDK",
        )
        assert second == []
        assert len(notices.rows) == 1
        assert snapshot_from(rows)["v1"] == "REJECTED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_new_bad_states_ignore_ready_for_sale() -> None:
    row = ReviewVersion("1", "Mira", "com.a.mira", "1.0", "READY_FOR_SALE", "v1")
    assert new_bad_states({}, [row]) == []
    rejected = ReviewVersion("1", "Mira", "com.a.mira", "1.0", "INVALID_BINARY", "v1")
    assert new_bad_states({"v1": "INVALID_BINARY"}, [rejected]) == []
    assert new_bad_states({}, [rejected]) == [rejected]


def test_format_notice_names_missing_gmail() -> None:
    row = ReviewVersion("1", "Mira", "com.a.mira", "2.0", "REJECTED", "v9")
    assert "No Apple rejection mail in gmail_archive for Mira." in format_notice(row, "")


def test_print_reviews_returns_nonzero_for_rejected(capsys) -> None:
    row = ReviewVersion("1", "OL Golf", "com.darrenoakey.olGolf", "1.0.2", "REJECTED", "v1")
    assert print_reviews([row]) == 1
    output = capsys.readouterr().out
    assert "OL Golf" in output
    assert "REJECTED" in output


def test_collect_review_status_includes_live_groupiecam() -> None:
    rows = collect_review_status(apple_request)
    names = {row.name for row in rows}
    assert "GroupieCam" in names
