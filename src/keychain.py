# keychain.py — prompt-free macOS Keychain access for scripts.
#
# WHY NOT `import keyring`:
#   keyring talks to the login keychain *in-process*. macOS then authorises the
#   call by checking the *calling binary's* code signature (cdhash) against each
#   item's ACL. Homebrew's Python is ad-hoc signed, so every version bump
#   (3.14.4 -> 3.14.6 -> ...) is a brand-new identity that isn't in the ACL, and
#   the item's keychain "partition list" blocks non-Apple binaries regardless.
#   Result: a GUI password prompt after every Python upgrade — useless in a script.
#
# THE FIX:
#   Shell out to /usr/bin/security. Its code requirement
#   (`identifier "com.apple.security" and anchor apple`) never changes across
#   Python or macOS upgrades, and it lives in Apple's default keychain
#   partition. What keeps reads prompt-free is that stable identity on items
#   /usr/bin/security itself created (measured: E5, E7). Writes still pass -A,
#   but -A opens only the decrypt ACL (Gate 1); it does NOT admit foreign
#   in-process callers — the partition list (Gate 2) denies them identically
#   either way (E4). -A is kept as deliberate defence-in-depth so a future
#   re-signing of `security` cannot strand items (plan §3.1).
#
# Drop-in for the two keyring calls app-publish used: get_password / set_password.

import os
import signal
import subprocess

SECURITY = "/usr/bin/security"
TIMEOUT_S = 10  # §3.4.1: a dialog-hung security ignores SIGTERM; hard-kill it.


class SecurityTimeout(TimeoutError):
    """security(1) exceeded the hard timeout; its process group was SIGKILLed."""

    def __init__(self, argv0: str, timeout_s: float, pgid: int):
        super().__init__(
            f"{argv0} timed out after {timeout_s}s; process group {pgid} SIGKILLed"
        )
        self.pgid = pgid


# ##################################################################
# run group killed
# spawn argv in its own process group; on timeout SIGKILL the whole group
def _run_group_killed(
    argv: list[str], timeout_s: float = TIMEOUT_S
) -> subprocess.CompletedProcess:
    # §3.4.1: killing only the pid leaves a dialog-hung child alive with the
    # dialog still up; stdin is /dev/null so security can never wait on us.
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise SecurityTimeout(argv[0], timeout_s, proc.pid) from None
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


# ##################################################################
# security
# run /usr/bin/security with the hardened group-kill timeout
def _security(args: list[str]) -> subprocess.CompletedProcess:
    return _run_group_killed([SECURITY, *args])


# ##################################################################
# get password
# read a secret via the trusted /usr/bin/security binary (no GUI prompt)
def get_password(service: str, account: str) -> str | None:
    r = _security(
        ["find-generic-password", "-s", service, "-a", account, "-w"]
    )
    if r.returncode != 0:
        return None
    # -w prints the value plus a single trailing newline; strip only that.
    return r.stdout.rstrip("\n") or None


# ##################################################################
# set password
# write a secret via /usr/bin/security (its stable identity keeps reads prompt-free)
def set_password(service: str, account: str, value: str) -> None:
    # Delete first so the recreated item gets a fresh allow-all ACL; -U alone
    # does not reset an existing item's per-app ACL.
    _security(["delete-generic-password", "-s", service, "-a", account])
    r = _security(
        [
            "add-generic-password",
            "-s",
            service,
            "-a",
            account,
            "-l",
            service,
            "-w",
            value,
            "-A",
        ]
    )
    if r.returncode != 0:
        # Never embed r.args here: argv contains the secret (-w <value>).
        raise RuntimeError(
            f"add-generic-password failed for {service}/{account} "
            f"(exit {r.returncode}): {r.stderr.strip()}"
        )
