# live App Store Connect review-state watch and Beezle notice
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from apple_portal import request as apple_request
from config import SRC_ROOT
from notices import NoticeStore, PostgresNoticeStore
from rejection import lookup_rejection
from utils import ensure_dir, print_error, print_header, print_info, print_warning

BAD_STATES = frozenset(
    {
        "REJECTED",
        "INVALID_BINARY",
        "METADATA_REJECTED",
        "DEVELOPER_REJECTED",
        "UNRESOLVED_ISSUES",
    }
)

SNAPSHOT_PATH = SRC_ROOT / "output" / "review-status.json"


@dataclass(frozen=True)
class ReviewVersion:
    app_id: str
    name: str
    bundle_id: str
    version: str
    state: str
    version_id: str


# ##################################################################
# collect review status
# JWT GET /v1/apps with appStoreVersions — this is the live ASC state
def collect_review_status(request_fn=apple_request) -> list[ReviewVersion]:
    document = request_fn("GET", "apps", params={"limit": 200, "include": "appStoreVersions"})
    versions_by_id = {
        item["id"]: item for item in document.get("included") or [] if item.get("type") == "appStoreVersions"
    }
    rows: list[ReviewVersion] = []
    for app in document.get("data") or []:
        attributes = app.get("attributes") or {}
        refs = ((app.get("relationships") or {}).get("appStoreVersions") or {}).get("data") or []
        for ref in refs:
            version = versions_by_id.get(ref["id"])
            if version is None:
                continue
            version_attributes = version.get("attributes") or {}
            rows.append(
                ReviewVersion(
                    app_id=str(app["id"]),
                    name=str(attributes.get("name") or ""),
                    bundle_id=str(attributes.get("bundleId") or ""),
                    version=str(version_attributes.get("versionString") or ""),
                    state=str(version_attributes.get("appVersionState") or ""),
                    version_id=str(version["id"]),
                )
            )
    return rows


# ##################################################################
# snapshot from
# compact map of version id to current review state
def snapshot_from(rows: list[ReviewVersion]) -> dict[str, str]:
    return {row.version_id: row.state for row in rows}


# ##################################################################
# load snapshot
# previous watch result; missing file means first run
def load_snapshot(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"review snapshot is not an object: {path}")
    return {str(key): str(value) for key, value in payload.items()}


# ##################################################################
# save snapshot
# persist current states so the next watch only notices transitions
def save_snapshot(path: Path, snapshot: dict[str, str]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")


# ##################################################################
# new bad states
# versions that just entered a rejected / invalid / unresolved state
def new_bad_states(previous: dict[str, str], rows: list[ReviewVersion]) -> list[ReviewVersion]:
    found: list[ReviewVersion] = []
    for row in rows:
        if row.state not in BAD_STATES:
            continue
        if previous.get(row.version_id) == row.state:
            continue
        found.append(row)
    return found


# ##################################################################
# format notice
# one Beezle line: app, version, state, and the Apple mail reason
def format_notice(row: ReviewVersion, reason: str) -> str:
    reason = reason.strip() or (f"No Apple rejection mail in gmail_archive for {row.name}.")
    return f"{row.name} {row.version} is {row.state}. {reason}"


# ##################################################################
# print reviews
# ./run review — live ASC states for every app
def print_reviews(rows: list[ReviewVersion] | None = None) -> int:
    rows = collect_review_status() if rows is None else rows
    print_header("APP STORE CONNECT REVIEW", "cyan")
    if not rows:
        print_warning("No appStoreVersions returned")
        return 1
    worst = 0
    for row in rows:
        line = f"{row.name}  {row.bundle_id}  {row.version}  {row.state}"
        if row.state in BAD_STATES:
            print_error(line)
            worst = 1
        else:
            print_info(line)
    return worst


# ##################################################################
# watch reviews
# diff against the last snapshot, ping Beezle for new bad states, save snapshot
def watch_reviews(
    snapshot_path: Path = SNAPSHOT_PATH,
    request_fn=apple_request,
    notices: NoticeStore | None = None,
    lookup=lookup_rejection,
) -> list[ReviewVersion]:
    rows = collect_review_status(request_fn)
    previous = load_snapshot(snapshot_path)
    notices = notices or PostgresNoticeStore()
    transitions = new_bad_states(previous, rows)
    for row in transitions:
        reason = lookup(row.name)
        notices.notify(
            "app-publish",
            f"{row.app_id}:{row.version_id}:{row.state}",
            format_notice(row, reason),
        )
    save_snapshot(snapshot_path, snapshot_from(rows))
    return transitions
