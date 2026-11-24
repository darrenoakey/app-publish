import json

import pytest

from followthrough import DeliveryError, mail_findings, purge_retired_state, deliver
from modules.detect import run as detect
from retirement import RETIRED_APPS, retirement_for
from state import ProjectState


def test_exact_retirement_identities_do_not_match_active_apps() -> None:
    assert {(a.app_id, a.bundle_id, a.name) for a in RETIRED_APPS} == {
        ("6759585788", "com.darrenoakey.olVideos", "OL Videos"),
        ("1608809460", "com.darrenoakey.dazmusic", "daz_music"),
    }
    assert retirement_for(app_id="6759585788").name == "OL Videos"
    assert retirement_for(bundle_id="com.darrenoakey.dazmusic").name == "daz_music"
    assert retirement_for(name="OL Videos Extra") is None
    assert retirement_for(bundle_id="com.darrenoakey.olVideos2") is None
    assert retirement_for(app_id="16088094600") is None


def test_detect_blocks_each_retired_project_before_mutating_state(tmp_path, capsys) -> None:
    for retired in RETIRED_APPS:
        project = tmp_path / retired.name
        project.mkdir()
        (project / "index.html").write_text("retired source remains intact")
        state = ProjectState(bundle_id=retired.bundle_id)
        assert detect(project, state) is False
        assert not state.completed_steps
        assert not (project / ".app-publish.json").exists()
        assert retired.bundle_id in capsys.readouterr().out


def test_retired_mail_is_ignored_but_active_mail_is_preserved() -> None:
    mails = [
        {
            "id": "r",
            "subject": "You have a message from App Review about OL Videos (iOS).",
            "body": "",
            "received": "x",
        },
        {"id": "d", "subject": "Your uploaded build for daz_music has one or more issues", "body": "", "received": "x"},
        {"id": "a", "subject": "You have a message from App Review about Mira (iOS).", "body": "", "received": "x"},
    ]
    assert [finding.ref for finding in mail_findings(mails, {"Mira": "active"})] == ["gmail:a"]


def test_stale_retired_pending_delivery_is_purged_and_cannot_be_sent(tmp_path) -> None:
    fingerprint = "review_rejected|6759585788|1|9|REJECTED|v1"
    state = {
        "schema": 2,
        "seen": {fingerprint: "old"},
        "mail_seen": {},
        "blockers": {},
        "mail_cursor": None,
        "pending": {
            fingerprint: {
                "marker": "retired-marker",
                "line": "review_rejected: OL Videos (6759585788)",
                "app": "OL Videos",
                "episode": "old",
            }
        },
        "sending": {},
        "delivered": {},
        "coverage_gaps": {},
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    assert purge_retired_state(state) == 2
    assert not state["pending"] and not state["seen"]
    with pytest.raises(DeliveryError, match="unknown"):
        deliver(path, ["retired-marker"], "conv-owner", "conv-sender")
