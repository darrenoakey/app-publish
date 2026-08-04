import os
import subprocess
import time
from uuid import uuid4

from keychain import SECURITY, SecurityTimeout, _run_group_killed, get_password


def test_missing_item_read_uses_macos_security_without_writing() -> None:
    assert SECURITY == "/usr/bin/security"
    assert get_password(f"app-publish-read-check-{uuid4()}", "absent") is None


def test_timeout_group_kills_child_and_grandchild() -> None:
    # /bin/sh forks /bin/sleep 300 into the same process group; a correct
    # timeout must SIGKILL the whole group, not just the direct child (§3.4.1).
    start = time.monotonic()
    pgid = None
    try:
        _run_group_killed(["/bin/sh", "-c", "/bin/sleep 300"], timeout_s=1)
    except SecurityTimeout as e:
        pgid = e.pgid
    assert pgid is not None, "expected SecurityTimeout"
    assert time.monotonic() - start < 11

    # Direct child provably dead (reaped): kill -0 fails.
    try:
        os.kill(pgid, 0)
        raise AssertionError(f"direct child {pgid} still alive")
    except ProcessLookupError:
        pass

    # Whole group empty — the sleep grandchild died too. Short window for the
    # kernel to finish reaping SIGKILLed orphans.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        r = subprocess.run(
            ["/usr/bin/pgrep", "-g", str(pgid)], capture_output=True, text=True
        )
        if r.returncode != 0:
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"process group {pgid} still has members: {r.stdout}")
