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
#   Python or macOS upgrades, and it lives in Apple's default keychain partition,
#   so it never prompts. Writes use -A so the stored item's ACL allows all apps
#   (no per-binary cdhash pinning) — which is what keeps reads prompt-free forever.
#
# Drop-in for the two keyring calls app-publish used: get_password / set_password.

import subprocess

SECURITY = "/usr/bin/security"


# ##################################################################
# get password
# read a secret via the trusted /usr/bin/security binary (no GUI prompt)
def get_password(service: str, account: str) -> str | None:
    r = subprocess.run(
        [SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None
    # -w prints the value plus a single trailing newline; strip only that.
    return r.stdout.rstrip("\n") or None


# ##################################################################
# set password
# write a secret with an allow-all ACL (-A) so it never re-prompts on upgrade
def set_password(service: str, account: str, value: str) -> None:
    # Delete first so the recreated item gets a fresh allow-all ACL; -U alone
    # does not reset an existing item's per-app ACL.
    subprocess.run(
        [SECURITY, "delete-generic-password", "-s", service, "-a", account],
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            SECURITY,
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
        ],
        check=True,
        capture_output=True,
        text=True,
    )
