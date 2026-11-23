# App Store Connect follow-through
# GET-only scan of every app's versions, review submissions, builds and Apple
# App Store Connect mail receipts. Each new finding stays pending under its
# stable fingerprint until an owner delivery is acknowledged; delivery posts an
# AgentD3 board direct exactly once (intent recorded before the post, board
# searched for the marker before any resend). It never mutates Apple or mail.
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import requests

from apple_portal import request as apple_request
from rejection import extract_rejection_text

STATE_PATH = Path.home() / "Library" / "Application Support" / "app-publish" / "followthrough-state.json"
AGENT_LINK_URL = "https://localhost:8900"
AGENT_LINK_CERTS = Path.home() / ".agent-link" / "certs" / "client"
BOARD_URL = "http://127.0.0.1:8620"
APPLE_MAIL_QUERY = "from:no_reply@email.apple.com"
STATE_SCHEMA = 2
MAIL_LOOKBACK_DAYS = 14
MAIL_MAX_RESULTS = 100
MAIL_RETENTION_DAYS = 45
BLOCKER_REPEAT_HOURS = 24
STALLED_REVIEW_DAYS = 7
STALLED_PROCESSING_HOURS = 6
APPS_LIMIT = 200
VERSIONS_LIMIT = 10
SUBMISSIONS_LIMIT = 20
BUILDS_LIMIT = 200
ASC_FETCH_WORKERS = 12
BOARD_PAGE_LIMIT = 200
BOARD_SEARCH_MAX_PAGES = 50
BOARD_SEARCH_SLACK = dt.timedelta(minutes=10)
BOARD_DIRECT_MAX_RUNES = 2000
BOARD_RATE_RETRIES = 3
BOARD_RATE_WAIT_SECONDS = 6.0
LINE_MAX_CHARS = 600

# ##################################################################
# rate-limit pause
# blocking wait between board posts after a 429; no busy polling
_BOARD_RATE_TICK = threading.Event()


EXIT_QUIET = 0
EXIT_BLOCKED = 1
EXIT_ACTIONABLE = 2

REJECTED_VERSION_STATES = frozenset({"REJECTED", "METADATA_REJECTED", "INVALID_BINARY"})
VERSION_ATTENTION = {
    "PENDING_DEVELOPER_RELEASE": "awaiting_developer_release",
    "WAITING_FOR_EXPORT_COMPLIANCE": "export_compliance_required",
}
WAITING_SUBMISSION_STATES = frozenset({"WAITING_FOR_REVIEW", "IN_REVIEW"})
FAILED_BUILD_STATES = frozenset({"FAILED", "INVALID"})
MAIL_RULES = (
    ("review_message_receipt", re.compile(r"message from App Review about (?P<app>.+?) \(", re.IGNORECASE)),
    ("build_issue_receipt", re.compile(r"uploaded build for (?P<app>.+?) has one or more issues", re.IGNORECASE)),
    (
        "apple_issue_receipt",
        re.compile(r"(?P<app>)(?:noticed an issue|rejected|action needed|guideline)", re.IGNORECASE),
    ),
)

Request = Callable[..., dict]


@dataclass(frozen=True)
class Finding:
    kind: str
    app_id: str
    app: str
    version: str
    build: str
    state: str
    ref: str
    detail: str = ""

    def fingerprint(self) -> str:
        return "|".join((self.kind, self.app_id, self.version, self.build, self.state, self.ref))

    def line(self) -> str:
        build = f" build {self.build}" if self.build else ""
        version = f" v{self.version}" if self.version else ""
        detail = f" — {self.detail}" if self.detail else ""
        return f"{self.kind}: {self.app} ({self.app_id}){version}{build} {self.state} [{self.ref}]{detail}"[
            :LINE_MAX_CHARS
        ]


class FollowthroughBlocked(RuntimeError):
    def __init__(self, route: str, message: str, key: str = "") -> None:
        super().__init__(message)
        self.route = route
        self.key = key

    def fingerprint(self) -> str:
        if self.key:
            return f"blocked|{self.route}|{self.key}"
        cause = self.__cause__
        status = getattr(cause, "status_code", None) or getattr(getattr(cause, "response", None), "status_code", None)
        return f"blocked|{self.route}|{status or type(cause).__name__}"


class DeliveryError(RuntimeError):
    pass


# ##################################################################
# parse time
# Apple timestamps are ISO8601 with Z or numeric offsets
def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.UTC)


# ##################################################################
# delivery marker
# stable per fingerprint episode: a recurrence gets a new marker
def delivery_marker(fingerprint: str, episode: str) -> str:
    return "asc-ft-" + hashlib.sha256(f"{fingerprint}|{episode}".encode()).hexdigest()[:16]


# ##################################################################
# page coverage
# first page only; anything Apple says lies beyond it is an explicit gap
def page_coverage(source: str, document: dict, limit: int) -> dict:
    returned = len(document.get("data") or [])
    total = ((document.get("meta") or {}).get("paging") or {}).get("total")
    truncated = bool((document.get("links") or {}).get("next")) or (total is not None and int(total) > returned)
    truncated = truncated or (total is None and returned >= limit)
    return {"source": source, "returned": returned, "total": total, "limit": limit, "truncated": truncated}


# ##################################################################
# collect asc
# every app with its versions (and attached build), review submissions, builds
def _app_versions(request_fn: Request, app_id: str) -> dict:
    return request_fn(
        "GET",
        f"apps/{app_id}/appStoreVersions",
        params={
            "limit": VERSIONS_LIMIT,
            "include": "build",
            "fields[appStoreVersions]": "versionString,appVersionState,createdDate,build",
            "fields[builds]": "version,processingState",
        },
    )


def _app_submissions(request_fn: Request, app_id: str) -> dict:
    return request_fn(
        "GET",
        "reviewSubmissions",
        params={
            "filter[app]": app_id,
            "limit": SUBMISSIONS_LIMIT,
            "fields[reviewSubmissions]": "state,submittedDate",
        },
    )


def _builds_document(request_fn: Request) -> dict:
    return request_fn(
        "GET",
        "builds",
        params={
            "limit": BUILDS_LIMIT,
            "sort": "-uploadedDate",
            "include": "app,preReleaseVersion",
            "fields[builds]": "version,processingState,uploadedDate,expired,app,preReleaseVersion",
            "fields[preReleaseVersions]": "version",
        },
    )


def collect_asc(request_fn: Request = apple_request) -> dict:
    # App Store Connect answers one GET per call over ~1s of round-trip latency
    # and every per-app request is independent, so the whole scan is fetched
    # concurrently. The requests are the same real GETs in the same set; only
    # their ordering changes, and results are keyed back per app and reassembled
    # in Apple's order so the snapshot is byte-identical to a serial scan.
    apps_document = request_fn("GET", "apps", params={"limit": APPS_LIMIT, "fields[apps]": "name,bundleId"})
    apps = apps_document.get("data") or []
    if not apps:
        raise FollowthroughBlocked("asc-api", "App Store Connect returned zero apps; refusing to treat as empty")
    jobs: list[tuple[str, Callable[[], dict]]] = []
    for app in apps:
        app_id = str(app["id"])
        jobs.append((f"versions:{app_id}", lambda app_id=app_id: _app_versions(request_fn, app_id)))
        jobs.append((f"submissions:{app_id}", lambda app_id=app_id: _app_submissions(request_fn, app_id)))
    jobs.append(("builds", lambda: _builds_document(request_fn)))
    documents: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(ASC_FETCH_WORKERS, len(jobs))) as pool:
        futures = {key: pool.submit(job) for key, job in jobs}
        for key, future in futures.items():
            documents[key] = future.result()

    coverage = [page_coverage("apps", apps_document, APPS_LIMIT)]
    inventory = []
    for app in apps:
        app_id = str(app["id"])
        name = str((app.get("attributes") or {}).get("name") or "")
        versions = documents[f"versions:{app_id}"]
        coverage.append(page_coverage(f"appStoreVersions:{name} ({app_id})", versions, VERSIONS_LIMIT))
        builds_by_id = {item["id"]: item for item in versions.get("included") or [] if item.get("type") == "builds"}
        submissions = documents[f"submissions:{app_id}"]
        coverage.append(page_coverage(f"reviewSubmissions:{name} ({app_id})", submissions, SUBMISSIONS_LIMIT))
        inventory.append(
            {
                "app_id": app_id,
                "name": name,
                "bundle_id": str((app.get("attributes") or {}).get("bundleId") or ""),
                "versions": [_version_row(item, builds_by_id) for item in versions.get("data") or []],
                "submissions": [
                    {"id": item["id"], **(item.get("attributes") or {})} for item in submissions.get("data") or []
                ],
            }
        )
    builds = documents["builds"]
    coverage.append(page_coverage("builds", builds, BUILDS_LIMIT))
    marketing = {
        item["id"]: str((item.get("attributes") or {}).get("version") or "")
        for item in builds.get("included") or []
        if item.get("type") == "preReleaseVersions"
    }
    build_rows = []
    for item in builds.get("data") or []:
        relationships = item.get("relationships") or {}
        app_ref = ((relationships.get("app") or {}).get("data") or {}).get("id")
        pre_ref = ((relationships.get("preReleaseVersion") or {}).get("data") or {}).get("id")
        build_rows.append(
            {
                "id": item["id"],
                "app_id": str(app_ref or ""),
                "marketing": marketing.get(pre_ref, ""),
                **item["attributes"],
            }
        )
    return {"apps": inventory, "builds": build_rows, "coverage": coverage}


def _version_row(item: dict, builds_by_id: dict) -> dict:
    attributes = item.get("attributes") or {}
    build_ref = (((item.get("relationships") or {}).get("build") or {}).get("data") or {}).get("id")
    build = (builds_by_id.get(build_ref) or {}).get("attributes") or {}
    return {
        "id": item["id"],
        "version": str(attributes.get("versionString") or ""),
        "state": str(attributes.get("appVersionState") or ""),
        "created": attributes.get("createdDate"),
        "build": str(build.get("version") or ""),
        "build_state": str(build.get("processingState") or ""),
    }


# ##################################################################
# asc findings
# actionable review, release-gate, stalled and processing outcomes
def asc_findings(snapshot: dict, now: dt.datetime) -> list[Finding]:
    findings: list[Finding] = []
    names = {app["app_id"]: app["name"] for app in snapshot["apps"]}
    for app in snapshot["apps"]:
        current = max(app["versions"], key=lambda row: row["created"] or "", default={"version": "", "build": ""})
        for row in app["versions"]:
            kind = "review_rejected" if row["state"] in REJECTED_VERSION_STATES else VERSION_ATTENTION.get(row["state"])
            if kind:
                findings.append(
                    Finding(kind, app["app_id"], app["name"], row["version"], row["build"], row["state"], row["id"])
                )
        for submission in app["submissions"]:
            state = str(submission.get("state") or "")
            submitted = parse_time(submission.get("submittedDate"))
            if state == "UNRESOLVED_ISSUES":
                kind = "review_unresolved"
            elif (
                state in WAITING_SUBMISSION_STATES
                and submitted
                and now - submitted > dt.timedelta(days=STALLED_REVIEW_DAYS)
            ):
                kind = "review_stalled"
            else:
                continue
            detail = f"submitted {submission.get('submittedDate')}" if submitted else ""
            findings.append(
                Finding(
                    kind,
                    app["app_id"],
                    app["name"],
                    current["version"],
                    current["build"],
                    state,
                    submission["id"],
                    detail,
                )
            )
    for build in snapshot["builds"]:
        state = str(build.get("processingState") or "")
        uploaded = parse_time(build.get("uploadedDate"))
        if build.get("expired"):
            continue
        if state in FAILED_BUILD_STATES:
            kind = "build_processing_failed"
        elif state == "PROCESSING" and uploaded and now - uploaded > dt.timedelta(hours=STALLED_PROCESSING_HOURS):
            kind = "build_processing_stalled"
        else:
            continue
        findings.append(
            Finding(
                kind,
                build["app_id"],
                names.get(build["app_id"], ""),
                build.get("marketing", ""),
                str(build.get("version") or ""),
                state,
                build["id"],
                f"uploaded {build.get('uploadedDate')}",
            )
        )
    return findings


# ##################################################################
# agent link call
# read-only Gmail call through the existing agent-link mTLS client identity
def agent_link_call(function: str, body: dict) -> dict:
    response = requests.post(
        f"{AGENT_LINK_URL}/api/v1/call/mail/{function}",
        json=body,
        cert=(str(AGENT_LINK_CERTS / "client.crt"), str(AGENT_LINK_CERTS / "client.key")),
        verify=str(AGENT_LINK_CERTS / "ca.crt"),
        timeout=30,
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        raise ValueError(f"agent-link mail/{function} returned no source results")
    return results[0].get("Data") or {}


# ##################################################################
# collect mail
# Apple App Store Connect receipts after the cursor, ids not seen before;
# returns the mails plus first-page coverage of the search
def collect_mail(
    after: dt.datetime, seen: set[str], call: Callable[[str, dict], dict] = agent_link_call
) -> tuple[list[dict], dict]:
    query = f"{APPLE_MAIL_QUERY} after:{int(after.timestamp())}"
    result = call("search", {"query": query, "max_results": MAIL_MAX_RESULTS})
    messages = result.get("messages") or []
    coverage = {
        "source": "apple-mail",
        "returned": len(messages),
        "total": None,
        "limit": MAIL_MAX_RESULTS,
        "truncated": bool(result.get("nextPageToken")) or len(messages) >= MAIL_MAX_RESULTS,
    }
    out = []
    for message in messages:
        if message["id"] in seen:
            continue
        data = call("get-message", {"id": message["id"], "format": "full"})
        headers = {item["name"].lower(): item["value"] for item in (data.get("payload") or {}).get("headers") or []}
        out.append(
            {
                "id": message["id"],
                "thread": message.get("threadId", ""),
                "received": dt.datetime.fromtimestamp(int(data["internalDate"]) / 1000, dt.UTC).isoformat(),
                "subject": headers.get("subject", ""),
                "body": _mail_text(data.get("payload") or {}),
            }
        )
    return out, coverage


def _mail_text(payload: dict) -> str:
    parts = [payload, *(payload.get("parts") or [])]
    for wanted in ("text/plain", "text/html"):
        for part in parts:
            data = (part.get("body") or {}).get("data")
            if part.get("mimeType") == wanted and data:
                text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
                return extract_rejection_text(
                    text if wanted == "text/html" else "", text if wanted == "text/plain" else ""
                )
    return ""


# ##################################################################
# mail findings
# first-party receipts: new App Review messages and build issue notices
def mail_findings(mails: list[dict], app_ids: dict[str, str]) -> list[Finding]:
    findings = []
    for mail in mails:
        for kind, pattern in MAIL_RULES:
            match = pattern.search(mail["subject"])
            if not match:
                continue
            app = match.group("app") or ""
            detail = mail["subject"]
            if kind == "build_issue_receipt" and mail["body"]:
                detail = f"{detail} :: {' / '.join(mail['body'].splitlines()[:6])}"
            if kind == "review_message_receipt":
                detail = f"{detail} (generic receipt; message text needs the App Review UI)"
            findings.append(
                Finding(kind, app_ids.get(app, ""), app, "", "", mail["received"], f"gmail:{mail['id']}", detail)
            )
            break
    return findings


# ##################################################################
# state lock
# one writer at a time across the scheduled scan and any delivery process
@contextlib.contextmanager
def state_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ##################################################################
# load state
# missing file is a first run; schema 1 migrates; anything else fails closed
def load_state(path: Path) -> dict:
    if not path.is_file():
        return {
            "schema": STATE_SCHEMA,
            "seen": {},
            "mail_seen": {},
            "blockers": {},
            "mail_cursor": None,
            "pending": {},
            "sending": {},
            "delivered": {},
            "coverage_gaps": {},
        }
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or state.get("schema") not in (1, STATE_SCHEMA):
        raise ValueError(f"follow-through state has an unexpected shape: {path}")
    for key in ("seen", "mail_seen", "blockers"):
        if not isinstance(state.get(key), dict):
            raise ValueError(f"follow-through state is missing {key}: {path}")
    if state["schema"] == 1:
        return migrate_schema_1(state)
    for key in ("pending", "sending", "delivered", "coverage_gaps"):
        if not isinstance(state.get(key), dict):
            raise ValueError(f"follow-through state is missing {key}: {path}")
    return state


# ##################################################################
# migrate schema 1
# schema 1 printed every seen finding as NEW in its run output and tracked no
# owner delivery; carry each one forward as reported so nothing re-escalates
def migrate_schema_1(state: dict) -> dict:
    at = (state.get("last_run") or {}).get("at") or ""
    state["delivered"] = {
        fingerprint: {
            "episode": first_seen,
            "marker": delivery_marker(fingerprint, first_seen),
            "target": "",
            "message_id": "",
            "at": at,
            "disposition": "schema1-reported",
        }
        for fingerprint, first_seen in state["seen"].items()
    }
    state["pending"] = {}
    state["sending"] = {}
    state["coverage_gaps"] = {}
    state["schema"] = STATE_SCHEMA
    return state


# ##################################################################
# save state
# atomic replace so an interrupted run never leaves half a store
def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


# ##################################################################
# reconcile
# fresh findings become pending under a new episode; findings that disappear
# from a healthy route are forgotten so a recurrence is a new episode, but an
# undelivered pending entry survives until an owner delivery is acknowledged
def reconcile(
    state: dict, findings: list[Finding], now: dt.datetime, route_ok: dict[str, bool], baseline: bool = False
) -> list[Finding]:
    stamp = now.isoformat()
    fresh = [item for item in findings if item.fingerprint() not in state["seen"]]
    current = {item.fingerprint() for item in findings}
    kept = {}
    for fingerprint, first_seen in state["seen"].items():
        is_mail = "|gmail:" in fingerprint
        route = "mail" if is_mail else "asc"
        if fingerprint in current or not route_ok[route] or is_mail:
            kept[fingerprint] = first_seen
    for item in fresh:
        fingerprint = item.fingerprint()
        kept[fingerprint] = stamp
        state["sending"].pop(fingerprint, None)
        state["delivered"].pop(fingerprint, None)
        entry = {"episode": stamp, "marker": delivery_marker(fingerprint, stamp), "line": item.line(), "app": item.app}
        if baseline:
            state["delivered"][fingerprint] = {
                **entry,
                "target": "",
                "message_id": "",
                "at": stamp,
                "disposition": "baseline",
            }
        else:
            state["pending"][fingerprint] = entry
    horizon = now - dt.timedelta(days=MAIL_RETENTION_DAYS)
    state["seen"] = {key: value for key, value in kept.items() if "|gmail:" not in key or parse_time(value) > horizon}
    state["delivered"] = {
        key: value
        for key, value in state["delivered"].items()
        if key in state["seen"] or (parse_time(value.get("at")) or now) > horizon
    }
    return fresh


# ##################################################################
# throttle blockers
# a persistent outage or coverage gap is escalated once per BLOCKER_REPEAT_HOURS
def throttle_blockers(
    state: dict, blockers: list[FollowthroughBlocked], now: dt.datetime
) -> list[FollowthroughBlocked]:
    due = []
    for blocker in blockers:
        last = parse_time(state["blockers"].get(blocker.fingerprint()))
        if last is None or now - last >= dt.timedelta(hours=BLOCKER_REPEAT_HOURS):
            state["blockers"][blocker.fingerprint()] = now.isoformat()
            due.append(blocker)
    active = {blocker.fingerprint() for blocker in blockers}
    state["blockers"] = {key: value for key, value in state["blockers"].items() if key in active}
    return due


# ##################################################################
# coverage gaps
# a source whose first page did not hold everything is always reported; a gap
# escalates once when it first appears and is forgotten when it closes
def coverage_gaps(state: dict, coverage: list[dict], now: dt.datetime) -> tuple[list[str], bool]:
    lines, new_gap = [], False
    current = {}
    for item in coverage:
        if not item["truncated"]:
            continue
        total = item["total"] if item["total"] is not None else "unknown"
        since = state["coverage_gaps"].get(item["source"])
        current[item["source"]] = since or now.isoformat()
        tag = f"COVERAGE GAP (known since {since})" if since else "COVERAGE GAP (new)"
        new_gap = new_gap or since is None
        lines.append(
            f"{tag} {item['source']}: first page returned {item['returned']} of {total} "
            f"(limit {item['limit']}); later pages are not scanned"
        )
    scanned = {item["source"] for item in coverage}
    state["coverage_gaps"] = {
        **{key: value for key, value in state["coverage_gaps"].items() if key not in scanned},
        **current,
    }
    return lines, new_gap


def coverage_summary(coverage: list[dict]) -> str:
    def group(prefix: str) -> str:
        rows = [item for item in coverage if item["source"] == prefix or item["source"].startswith(prefix + ":")]
        if not rows:
            return f"{prefix} not scanned"
        cut = sum(1 for item in rows if item["truncated"])
        returned = sum(item["returned"] for item in rows)
        return f"{prefix} {returned} rows/{len(rows)} pages{f' ({cut} truncated)' if cut else ' complete'}"

    return "coverage: " + ", ".join(
        group(name) for name in ("apps", "appStoreVersions", "reviewSubmissions", "builds", "apple-mail")
    )


# ##################################################################
# run followthrough
# one scan: collect, classify, dedup, persist, and choose the exit code
def run_followthrough(
    state_path: Path = STATE_PATH,
    *,
    baseline: bool = False,
    now: dt.datetime | None = None,
    request_fn: Request = apple_request,
    mail_call: Callable[[str, dict], dict] = agent_link_call,
    evidence_path: Path | None = None,
) -> tuple[int, list[str]]:
    now = now or dt.datetime.now(dt.UTC)
    with state_lock(state_path):
        state = load_state(state_path)
        blockers: list[FollowthroughBlocked] = []
        findings: list[Finding] = []
        coverage: list[dict] = []
        snapshot = None
        try:
            snapshot = collect_asc(request_fn)
            coverage += snapshot["coverage"]
            findings += asc_findings(snapshot, now)
        except FollowthroughBlocked as error:
            blockers.append(error)
        except Exception as error:  # every ASC failure is an explicit blocker, never an empty success
            blocked = FollowthroughBlocked("asc-api", f"{type(error).__name__}: {error}")
            blocked.__cause__ = error
            blockers.append(blocked)
        app_ids = {app["name"]: app["app_id"] for app in (snapshot or {}).get("apps", [])}
        cursor = parse_time(state.get("mail_cursor")) or now - dt.timedelta(days=MAIL_LOOKBACK_DAYS)
        mails: list[dict] = []
        try:
            mails, mail_coverage = collect_mail(cursor - dt.timedelta(days=2), set(state["mail_seen"]), mail_call)
            coverage.append(mail_coverage)
            findings += mail_findings(mails, app_ids)
            state["mail_seen"].update({mail["id"]: mail["received"] for mail in mails})
            horizon = now - dt.timedelta(days=MAIL_RETENTION_DAYS)
            state["mail_seen"] = {
                key: value for key, value in state["mail_seen"].items() if parse_time(value) > horizon
            }
            if not mail_coverage["truncated"]:
                state["mail_cursor"] = now.isoformat()
        except Exception as error:  # mail route outage is its own blocker; ASC results still count
            blocked = FollowthroughBlocked("agent-link-mail", f"{type(error).__name__}: {error}")
            blocked.__cause__ = error
            blockers.append(blocked)
        gap_lines, new_gap = coverage_gaps(state, coverage, now)
        route_ok = {"asc": snapshot is not None, "mail": not any(b.route == "agent-link-mail" for b in blockers)}
        fresh = reconcile(state, findings, now, route_ok, baseline=baseline)
        due_blockers = throttle_blockers(state, blockers, now)
        state["last_run"] = {
            "at": now.isoformat(),
            "apps": len((snapshot or {}).get("apps", [])),
            "builds": len((snapshot or {}).get("builds", [])),
            "mails": len(mails),
            "findings": len(findings),
            "new": len(fresh),
            "pending": len(state["pending"]),
            "coverage": coverage,
            "blockers": [str(b) for b in blockers],
        }
        if evidence_path is not None:
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(
                json.dumps(
                    {
                        "at": now.isoformat(),
                        "snapshot": snapshot,
                        "findings": [f.__dict__ for f in findings],
                        "new": [f.fingerprint() for f in fresh],
                        "pending": state["pending"],
                        "coverage": coverage,
                        "blockers": [str(b) for b in blockers],
                        "mails": mails,
                    },
                    indent=2,
                    default=str,
                )
                + "\n"
            )
        save_state(state_path, state)
    fresh_prints = {item.fingerprint() for item in fresh}
    lines = [
        f"App Store Connect follow-through {now.strftime('%Y-%m-%dT%H:%M:%SZ')}: "
        f"{state['last_run']['apps']} apps, {state['last_run']['builds']} builds, {len(mails)} new Apple mails, "
        f"{len(findings)} open findings, {len(fresh)} new, {len(state['pending'])} pending delivery, "
        f"{len(blockers)} blockers",
        coverage_summary(coverage),
        *gap_lines,
    ]
    for blocker in blockers:
        tag = "BLOCKED" if blocker in due_blockers else "BLOCKED (already escalated within 24h)"
        lines.append(f"{tag} {blocker.route}: {blocker}")
    for fingerprint, entry in state["pending"].items():
        label = "NEW" if fingerprint in fresh_prints else "PENDING"
        lines.append(f"{label} id={entry['marker']} {entry['line']}")
    if baseline:
        lines += [f"BASELINE {item.line()}" for item in fresh]
    lines += [
        f"KNOWN {item.line()}"
        for item in findings
        if item.fingerprint() in state["delivered"] and item.fingerprint() not in fresh_prints
    ]
    if state["pending"] and not baseline:
        lines.append(
            "DELIVER each pending id once: ./run followthrough --deliver ID [--deliver ID ...] --to OWNER_CONV --sender YOUR_CONV"
        )
    if baseline:
        return (EXIT_QUIET if snapshot is not None else EXIT_BLOCKED), lines
    if state["pending"]:
        return EXIT_ACTIONABLE, lines
    if due_blockers or new_gap:
        return EXIT_BLOCKED, lines
    return EXIT_QUIET, lines


# ##################################################################
# board client
# AgentD3 message board: post a direct, search directs newest-first
class BoardClient:
    def __init__(self, base_url: str = BOARD_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def post_direct(self, sender: str, target: str, body: str) -> int:
        payload = {"kind": "direct", "target_id": target, "sender_id": sender, "body": body}
        for attempt in range(BOARD_RATE_RETRIES + 1):
            response = requests.post(f"{self.base_url}/v1/board", json=payload, timeout=30)
            if response.status_code == 429 and attempt < BOARD_RATE_RETRIES:
                _BOARD_RATE_TICK.wait(BOARD_RATE_WAIT_SECONDS)
                continue
            if response.status_code != 202:
                raise DeliveryError(f"board post to {target} failed ({response.status_code}): {response.text[:300]}")
            return int(response.json()["message"]["id"])
        raise DeliveryError(f"board post to {target} stayed rate limited")

    def find_direct(self, target: str, marker: str, since: dt.datetime) -> int | None:
        after = 0
        for _ in range(BOARD_SEARCH_MAX_PAGES):
            params = {"kind": "direct", "limit": BOARD_PAGE_LIMIT, **({"after": after} if after else {})}
            response = requests.get(f"{self.base_url}/v1/board", params=params, timeout=30)
            if response.status_code != 200:
                raise DeliveryError(f"board search failed ({response.status_code}): {response.text[:300]}")
            messages = response.json().get("messages") or []
            for message in messages:
                if message.get("target") == target and marker in (message.get("text") or ""):
                    return int(message["id"])
            if not messages or parse_time(messages[-1]["created_at"]) < since:
                return None
            after = int(messages[-1]["id"])
        raise DeliveryError(f"board search for {marker} exceeded {BOARD_SEARCH_MAX_PAGES} pages; refusing to resend")


# ##################################################################
# delivery body
# one owner message; every finding carries its marker for recovery search
def delivery_bodies(entries: list[tuple[str, dict]]) -> list[list[tuple[str, dict]]]:
    chunks: list[list[tuple[str, dict]]] = []
    size = BOARD_DIRECT_MAX_RUNES
    for entry in entries:
        cost = len(entry[1]["line"]) + len(entry[1]["marker"]) + 8
        if not chunks or size + cost > BOARD_DIRECT_MAX_RUNES - 400:
            chunks.append([])
            size = 0
        chunks[-1].append(entry)
        size += cost
    return chunks


def render_body(chunk: list[tuple[str, dict]]) -> str:
    lines = [
        "App Store Connect follow-through (GET-only, task-71a212d30757b0cf). "
        "New finding(s) for you; nothing was changed in App Store Connect. No reply needed.",
    ]
    lines += [f"- [{entry['marker']}] {entry['line']}" for _, entry in chunk]
    lines.append("A review_message_receipt is Apple's generic notice; read the text in the App Review UI.")
    return "\n".join(lines)


# ##################################################################
# resolve ids
# a delivery id is the pending marker; already-delivered markers replay as no-ops
def _resolve(state: dict, ids: list[str]) -> tuple[list[tuple[str, dict]], list[str]]:
    by_marker = {entry["marker"]: (fingerprint, entry) for fingerprint, entry in state["pending"].items()}
    delivered = {entry.get("marker"): (fingerprint, entry) for fingerprint, entry in state["delivered"].items()}
    todo, lines = [], []
    for marker in dict.fromkeys(ids):
        if marker in by_marker:
            todo.append(by_marker[marker])
        elif marker in delivered:
            fingerprint, entry = delivered[marker]
            lines.append(
                f"ALREADY DELIVERED id={marker} to {entry.get('target') or '-'} "
                f"message {entry.get('message_id') or '-'} ({entry.get('disposition')})"
            )
        else:
            raise DeliveryError(f"unknown follow-through id {marker}")
    return todo, lines


def _mark_delivered(
    state: dict, fingerprint: str, entry: dict, target: str, message_id: int | None, at: str, how: str
) -> str:
    state["pending"].pop(fingerprint, None)
    state["sending"].pop(fingerprint, None)
    state["delivered"][fingerprint] = {
        "episode": entry["episode"],
        "marker": entry["marker"],
        "line": entry["line"],
        "app": entry.get("app", ""),
        "target": target,
        "message_id": "" if message_id is None else str(message_id),
        "at": at,
        "disposition": how,
    }
    if message_id is None:
        return f"ACKNOWLEDGED id={entry['marker']} ({how})"
    return f"DELIVERED id={entry['marker']} to {target} board message {message_id} ({how})"


# ##################################################################
# deliver
# exactly-once owner delivery: recover any recorded intent from the board,
# persist the intent for the rest, post, then acknowledge
def deliver(
    state_path: Path,
    ids: list[str],
    target: str,
    sender: str,
    *,
    board: BoardClient | None = None,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> tuple[int, list[str]]:
    if not ids or not target.strip() or not sender.strip():
        raise DeliveryError("deliver requires at least one id, --to and --sender")
    board = board or BoardClient()
    with state_lock(state_path):
        state = load_state(state_path)
        todo, lines = _resolve(state, ids)
        fresh: list[tuple[str, dict]] = []
        for fingerprint, entry in todo:
            intent = state["sending"].get(fingerprint)
            if intent and intent["marker"] == entry["marker"]:
                if intent["target"] != target:
                    raise DeliveryError(
                        f"id {entry['marker']} already has a delivery intent to {intent['target']}; refusing a second owner"
                    )
                found = board.find_direct(
                    target, entry["marker"], parse_time(intent["started_at"]) - BOARD_SEARCH_SLACK
                )
                if found is not None:
                    lines.append(
                        _mark_delivered(state, fingerprint, entry, target, found, now().isoformat(), "recovered")
                    )
                    save_state(state_path, state)
                    continue
            fresh.append((fingerprint, entry))
        started = now().isoformat()
        for fingerprint, entry in fresh:
            state["sending"][fingerprint] = {"marker": entry["marker"], "target": target, "started_at": started}
        save_state(state_path, state)
        for chunk in delivery_bodies(fresh):
            message_id = board.post_direct(sender, target, render_body(chunk))
            stamp = now().isoformat()
            for fingerprint, entry in chunk:
                lines.append(_mark_delivered(state, fingerprint, entry, target, message_id, stamp, "posted"))
            save_state(state_path, state)
    return EXIT_QUIET, lines


# ##################################################################
# acknowledge
# owner already told another way, or the app is out of scope: record why
def acknowledge(
    state_path: Path, ids: list[str], receipt: str, *, now: dt.datetime | None = None
) -> tuple[int, list[str]]:
    if not ids or not receipt.strip():
        raise DeliveryError("ack requires at least one id and --receipt")
    stamp = (now or dt.datetime.now(dt.UTC)).isoformat()
    with state_lock(state_path):
        state = load_state(state_path)
        todo, lines = _resolve(state, ids)
        for fingerprint, entry in todo:
            if fingerprint in state["sending"]:
                raise DeliveryError(f"id {entry['marker']} has an open delivery intent; finish it with --deliver")
            lines.append(
                _mark_delivered(state, fingerprint, entry, "", None, stamp, f"acknowledged: {receipt.strip()}")
            )
        save_state(state_path, state)
    return EXIT_QUIET, lines


# ##################################################################
# main
# ./run followthrough [--baseline] [--evidence PATH]
# ./run followthrough --deliver ID ... --to CONV --sender CONV
# ./run followthrough --ack ID ... --receipt TEXT
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="GET-only App Store Connect follow-through scan")
    parser.add_argument("--baseline", action="store_true", help="record current findings as known without escalating")
    parser.add_argument("--state", type=Path, default=STATE_PATH, help="durable dedup store")
    parser.add_argument("--evidence", type=Path, help="write the full scan evidence JSON here")
    parser.add_argument("--deliver", action="append", default=[], metavar="ID", help="pending id to deliver")
    parser.add_argument("--to", default="", help="owner conversation id for --deliver")
    parser.add_argument("--sender", default="", help="your live conversation id for --deliver")
    parser.add_argument("--board-url", default=BOARD_URL, help="AgentD3 base URL for --deliver")
    parser.add_argument("--ack", action="append", default=[], metavar="ID", help="pending id to acknowledge")
    parser.add_argument("--receipt", default="", help="why an --ack id needs no owner message")
    args = parser.parse_args(argv)
    try:
        if args.deliver:
            code, lines = deliver(args.state, args.deliver, args.to, args.sender, board=BoardClient(args.board_url))
        elif args.ack:
            code, lines = acknowledge(args.state, args.ack, args.receipt)
        else:
            code, lines = run_followthrough(args.state, baseline=args.baseline, evidence_path=args.evidence)
    except (DeliveryError, requests.RequestException) as error:
        print(f"DELIVERY FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_BLOCKED
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
