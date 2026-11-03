"""Fail-closed guard for the owner's working iPhone (Starbuck).

Starbuck may receive release installs and launches only. Tests, UI automation,
timing, screen recording and screenshots are refused there. A non-release
destination must be exactly one explicit iOS Simulator destination: platform
exactly ``iOS Simulator``, exactly one of ``id``/``name``, only known keys, no
duplicate keys, no empty values, no ``generic`` destinations. Anything else
(empty, malformed, ambiguous, unknown, physical, or an alias of Starbuck)
raises ProtectedDeviceError. Callers must never redirect the action onto
Starbuck.

CLI (used by the review-video shell guard; one source of truth):
    python3 -I device_guard.py test-destination <destination> [...]
exits 0 when every destination is an acceptable simulator, 3 otherwise.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable

STARBUCK_UDID = "00008150-000611360AC0401C"  # hardware UDID (devicectl, xcodebuild)
STARBUCK_COREDEVICE_ID = "BD3CD332-2BB1-5F19-B4ED-953A902E7417"  # CoreDevice identifier
STARBUCK_ALIASES = (STARBUCK_UDID, STARBUCK_COREDEVICE_ID)
RELEASE = "release"  # install/launch of a release build: allowed on Starbuck
SIMULATOR_PLATFORM = "iOS Simulator"
EXIT_REFUSED = 3

# Documented xcodebuild destination keys. Keys are matched exactly as Xcode spells
# them (``OS`` and ``arch`` are uppercase/lowercase as documented); ``os`` is also
# accepted and folded into ``OS`` so duplicates cannot hide behind a case change.
_CANONICAL_KEYS = {"platform": "platform", "id": "id", "name": "name", "OS": "OS", "os": "OS", "arch": "arch"}
_OS_VALUE = re.compile(r"^(latest|\d+(\.\d+)*)$")
_ARCH_VALUES = frozenset({"arm64", "x86_64"})


class ProtectedDeviceError(RuntimeError):
    """Raised when an action targets Starbuck or is not an explicit simulator destination."""


def _normalised(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower())


def names_starbuck(text: str) -> bool:
    """True if text names Starbuck by name or by any known identifier, in any spelling."""
    if re.search(r"starbuck", text, re.IGNORECASE):
        return True
    norm = _normalised(text)
    return any(_normalised(alias) in norm for alias in STARBUCK_ALIASES)


def parse_destination(destination: str) -> dict[str, str]:
    """Parse an xcodebuild-style destination; refuse anything malformed, unknown or ambiguous."""
    if not destination or not destination.strip():
        raise ProtectedDeviceError("empty destination refused")
    fields: dict[str, str] = {}
    for raw in destination.split(","):
        key, sep, value = raw.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value:
            raise ProtectedDeviceError(f"malformed destination field {raw!r} in {destination!r}")
        if key not in _CANONICAL_KEYS:
            raise ProtectedDeviceError(f"unknown destination key {key!r} in {destination!r}")
        canonical = _CANONICAL_KEYS[key]
        if canonical in fields:
            raise ProtectedDeviceError(f"ambiguous destination: duplicate {canonical!r} in {destination!r}")
        if canonical == "OS" and not _OS_VALUE.match(value):
            raise ProtectedDeviceError(f"malformed OS value {value!r} in {destination!r}")
        if canonical == "arch" and value not in _ARCH_VALUES:
            raise ProtectedDeviceError(f"unknown arch {value!r} in {destination!r}")
        fields[canonical] = value
    return fields


def validate_simulator_destination(destination: str) -> None:
    """Accept only one explicit, well-formed iOS Simulator destination; raise otherwise."""
    if not destination or not destination.strip():
        raise ProtectedDeviceError("empty destination refused")
    if names_starbuck(destination):
        raise ProtectedDeviceError(f"Starbuck may not be used for tests: {destination!r}")
    if "generic" in destination.lower():
        raise ProtectedDeviceError(f"generic destination refused: {destination!r}")
    fields = parse_destination(destination)
    if fields.get("platform") != SIMULATOR_PLATFORM:
        raise ProtectedDeviceError(f"test destination must have platform={SIMULATOR_PLATFORM!r}, got {destination!r}")
    if ("id" in fields) == ("name" in fields):
        raise ProtectedDeviceError(f"simulator destination must name exactly one of id or name: {destination!r}")


def is_simulator_destination(destination: str) -> bool:
    try:
        validate_simulator_destination(destination)
    except ProtectedDeviceError:
        return False
    return True


def assert_test_destination(destination: str) -> None:
    """Test/record/timing/video destinations must be exactly one explicit simulator destination."""
    validate_simulator_destination(destination)


def assert_release_only(identifiers: Iterable[str], purpose: str) -> None:
    """Gate a device action by purpose.

    An empty target list or an empty identifier always refuses (fail closed).
    RELEASE may target Starbuck. Any other purpose must target only explicit
    simulator destinations.
    """
    idents = list(identifiers)
    if not idents:
        raise ProtectedDeviceError(f"no destination given for {purpose!r}; refusing (fail closed)")
    for ident in idents:
        if not ident or not ident.strip():
            raise ProtectedDeviceError(f"empty destination given for {purpose!r}; refusing")
        if purpose == RELEASE:
            continue
        validate_simulator_destination(ident)


def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "test-destination":
        print("usage: device_guard.py test-destination <destination> [...]", file=sys.stderr)
        return EXIT_REFUSED
    try:
        assert_release_only(argv[1:], "test")
    except ProtectedDeviceError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return EXIT_REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
