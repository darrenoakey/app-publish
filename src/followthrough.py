# App Store Connect follow-through
# GET-only scan of every app's versions, review submissions, builds and Apple
# App Store Connect mail receipts; reports only new actionable findings using a
# durable fingerprint store. It never mutates Apple, mail, or app state.
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from apple_portal import request as apple_request
from rejection import extract_rejection_text

STATE_PATH = Path.home() / "Library" / "Application Support" / "app-publish" / "followthrough-state.json"
AGENT_LINK_URL = "https://localhost:8900"
AGENT_LINK_CERTS = Path.home() / ".agent-link" / "certs" / "client"
APPLE_MAIL_QUERY = "from:no_reply@email.apple.com"
STATE_SCHEMA = 1
MAIL_LOOKBACK_DAYS = 14
MAIL_RETENTION_DAYS = 45
BLOCKER_REPEAT_HOURS = 24
STALLED_REVIEW_DAYS = 7
STALLED_PROCESSING_HOURS = 6

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
        return f"{self.kind}: {self.app} ({self.app_id}){version}{build} {self.state} [{self.ref}]{detail}"


class FollowthroughBlocked(RuntimeError):
    def __init__(self, route: str, message: str) -> None:
        super().__init__(message)
        self.route = route

    def fingerprint(self) -> str:
        cause = self.__cause__
        status = getattr(cause, "status_code", None) or getattr(getattr(cause, "response", None), "status_code", None)
        return f"blocked|{self.route}|{status or type(cause).__name__}"


# ##################################################################
# parse time
# Apple timestamps are ISO8601 with Z or numeric offsets
def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.UTC)


# ##################################################################
# collect asc
# every app with its versions (and attached build), review submissions, builds
def collect_asc(request_fn: Request = apple_request) -> dict:
    apps = request_fn("GET", "apps", params={"limit": 200, "fields[apps]": "name,bundleId"}).get("data") or []
    if not apps:
        raise FollowthroughBlocked("asc-api", "App Store Connect returned zero apps; refusing to treat as empty")
    inventory = []
    for app in apps:
        app_id = str(app["id"])
        versions = request_fn(
            "GET",
            f"apps/{app_id}/appStoreVersions",
            params={
                "limit": 10,
                "include": "build",
                "fields[appStoreVersions]": "versionString,appVersionState,createdDate,build",
                "fields[builds]": "version,processingState",
            },
        )
        builds_by_id = {item["id"]: item for item in versions.get("included") or [] if item.get("type") == "builds"}
        submissions = request_fn(
            "GET",
            "reviewSubmissions",
            params={"filter[app]": app_id, "limit": 20, "fields[reviewSubmissions]": "state,submittedDate"},
        )
        inventory.append(
            {
                "app_id": app_id,
                "name": str((app.get("attributes") or {}).get("name") or ""),
                "bundle_id": str((app.get("attributes") or {}).get("bundleId") or ""),
                "versions": [_version_row(item, builds_by_id) for item in versions.get("data") or []],
                "submissions": [
                    {"id": item["id"], **(item.get("attributes") or {})} for item in submissions.get("data") or []
                ],
            }
        )
    builds = request_fn(
        "GET",
        "builds",
        params={
            "limit": 200,
            "sort": "-uploadedDate",
            "include": "app,preReleaseVersion",
            "fields[builds]": "version,processingState,uploadedDate,expired,app,preReleaseVersion",
            "fields[preReleaseVersions]": "version",
        },
    )
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
    return {"apps": inventory, "builds": build_rows}


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
# Apple App Store Connect receipts after the cursor, ids not seen before
def collect_mail(after: dt.datetime, seen: set[str], call: Callable[[str, dict], dict] = agent_link_call) -> list[dict]:
    query = f"{APPLE_MAIL_QUERY} after:{int(after.timestamp())}"
    messages = call("search", {"query": query, "max_results": 100}).get("messages") or []
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
    return out


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
# load state
# missing file is a first run; anything malformed fails closed
def load_state(path: Path) -> dict:
    if not path.is_file():
        return {"schema": STATE_SCHEMA, "seen": {}, "mail_seen": {}, "blockers": {}, "mail_cursor": None}
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA:
        raise ValueError(f"follow-through state has an unexpected shape: {path}")
    for key in ("seen", "mail_seen", "blockers"):
        if not isinstance(state.get(key), dict):
            raise ValueError(f"follow-through state is missing {key}: {path}")
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
# split current findings into new and known; findings that disappeared are
# forgotten so a recurrence is reported again
def reconcile(state: dict, findings: list[Finding], now: dt.datetime, route_ok: dict[str, bool]) -> list[Finding]:
    stamp = now.isoformat()
    fresh = [item for item in findings if item.fingerprint() not in state["seen"]]
    current = {item.fingerprint() for item in findings}
    kept = {}
    for fingerprint, first_seen in state["seen"].items():
        is_mail = "|gmail:" in fingerprint
        route = "mail" if is_mail else "asc"
        if fingerprint in current or not route_ok[route] or is_mail:
            kept[fingerprint] = first_seen
    for item in findings:
        kept.setdefault(item.fingerprint(), stamp)
    horizon = now - dt.timedelta(days=MAIL_RETENTION_DAYS)
    state["seen"] = {key: value for key, value in kept.items() if "|gmail:" not in key or parse_time(value) > horizon}
    return fresh


# ##################################################################
# throttle blockers
# a persistent outage is escalated once per BLOCKER_REPEAT_HOURS
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
    state = load_state(state_path)
    blockers: list[FollowthroughBlocked] = []
    findings: list[Finding] = []
    snapshot = None
    try:
        snapshot = collect_asc(request_fn)
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
        mails = collect_mail(cursor - dt.timedelta(days=2), set(state["mail_seen"]), mail_call)
        findings += mail_findings(mails, app_ids)
        state["mail_seen"].update({mail["id"]: mail["received"] for mail in mails})
        horizon = now - dt.timedelta(days=MAIL_RETENTION_DAYS)
        state["mail_seen"] = {key: value for key, value in state["mail_seen"].items() if parse_time(value) > horizon}
        state["mail_cursor"] = now.isoformat()
    except Exception as error:  # mail route outage is its own blocker; ASC results still count
        blocked = FollowthroughBlocked("agent-link-mail", f"{type(error).__name__}: {error}")
        blocked.__cause__ = error
        blockers.append(blocked)
    route_ok = {"asc": snapshot is not None, "mail": not any(b.route == "agent-link-mail" for b in blockers)}
    fresh = reconcile(state, findings, now, route_ok)
    due_blockers = throttle_blockers(state, blockers, now)
    state["last_run"] = {
        "at": now.isoformat(),
        "apps": len((snapshot or {}).get("apps", [])),
        "builds": len((snapshot or {}).get("builds", [])),
        "mails": len(mails),
        "findings": len(findings),
        "new": len(fresh),
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
                    "blockers": [str(b) for b in blockers],
                    "mails": mails,
                },
                indent=2,
                default=str,
            )
            + "\n"
        )
    save_state(state_path, state)
    lines = [
        f"App Store Connect follow-through {now.strftime('%Y-%m-%dT%H:%M:%SZ')}: "
        f"{state['last_run']['apps']} apps, {state['last_run']['builds']} builds, {len(mails)} new Apple mails, "
        f"{len(findings)} open findings, {len(fresh)} new, {len(blockers)} blockers"
    ]
    for blocker in blockers:
        tag = "BLOCKED" if blocker in due_blockers else "BLOCKED (already escalated within 24h)"
        lines.append(f"{tag} {blocker.route}: {blocker}")
    label = "BASELINE" if baseline else "NEW"
    lines += [f"{label} {item.line()}" for item in fresh]
    lines += [f"KNOWN {item.line()}" for item in findings if item not in fresh]
    if baseline:
        return EXIT_QUIET if snapshot is not None else EXIT_BLOCKED, lines
    if fresh:
        return EXIT_ACTIONABLE, lines
    if due_blockers:
        return EXIT_BLOCKED, lines
    return EXIT_QUIET, lines


# ##################################################################
# main
# ./run followthrough [--baseline] [--evidence PATH]
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="GET-only App Store Connect follow-through scan")
    parser.add_argument("--baseline", action="store_true", help="record current findings as known without escalating")
    parser.add_argument("--state", type=Path, default=STATE_PATH, help="durable dedup store")
    parser.add_argument("--evidence", type=Path, help="write the full scan evidence JSON here")
    args = parser.parse_args(argv)
    code, lines = run_followthrough(args.state, baseline=args.baseline, evidence_path=args.evidence)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
