"""Provision and apply Apple signatures without using macOS Keychain."""

from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).parent.parent))

import apple_portal
from config import TEAM_ID
from state import ProjectState
from utils import (
    print_error,
    print_success,
    run as exec_cmd,
    secret_file_argument,
)


def has_distribution_cert() -> bool:
    """Report whether the encrypted provider contains a usable remote identity."""
    value = apple_portal.secrets_store.get_secret_bytes(apple_portal.SERVICE, apple_portal.P12_ACCOUNT)
    if not value:
        return False
    try:
        _, certificate = apple_portal._load_p12(value)
        return apple_portal._remote_certificate_id(certificate) is not None
    except apple_portal.ApplePortalError:
        return False


def ensure_bundle_id(bundle_id: str, app_name: str) -> bool:
    try:
        apple_portal.ensure_bundle_id(bundle_id, app_name)
    except apple_portal.ApplePortalError as error:
        print_error(f"Bundle ID check failed: {error}")
        return False
    print_success(f"Bundle ID ready: {bundle_id}")
    return True


def ensure_distribution_cert() -> bool:
    try:
        identity = apple_portal.ensure_distribution_identity()
    except apple_portal.ApplePortalError as error:
        print_error(f"Distribution identity setup failed: {error}")
        return False
    name = identity.certificate.subject.rfc4514_string()
    print_success(f"Encrypted Apple Distribution identity ready: {name}")
    return True


def _rcodesign_binary() -> str:
    discovered = shutil.which("rcodesign")
    if discovered:
        return discovered
    installed = Path.home() / ".cargo" / "bin" / "rcodesign"
    if installed.is_file():
        return str(installed)
    raise apple_portal.ApplePortalError("rcodesign is not installed; install the apple-codesign package")


def _profile_entitlements(profile: bytes) -> bytes:
    decoded = subprocess.run(
        [
            "/usr/bin/openssl",
            "smime",
            "-inform",
            "der",
            "-verify",
            "-noverify",
        ],
        input=profile,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if decoded.returncode != 0:
        detail = decoded.stderr.decode("utf-8", errors="replace").strip()
        raise apple_portal.ApplePortalError(f"could not decode provisioning profile: {detail}")
    document = plistlib.loads(decoded.stdout)
    entitlements = document.get("Entitlements")
    if not isinstance(entitlements, dict):
        raise apple_portal.ApplePortalError("provisioning profile has no entitlements dictionary")
    return plistlib.dumps(entitlements, fmt=plistlib.FMT_XML)


def sign_app_bundle(
    app_bundle: Path,
    bundle_id: str,
    app_name: str,
    profile_type: str,
    *,
    device_udids: list[str] | None = None,
) -> bool:
    """Embed a current profile and recursively sign with an in-memory P12."""
    try:
        identity = apple_portal.ensure_distribution_identity()
        bundles = [(app_bundle, bundle_id, "main")]
        nested = sorted(
            (
                path
                for path in app_bundle.rglob("*")
                if path.is_dir() and path != app_bundle and path.suffix in {".app", ".appex"}
            ),
            key=lambda path: len(path.parts),
        )
        for bundle in nested:
            info_path = bundle / "Info.plist"
            if not info_path.is_file():
                raise apple_portal.ApplePortalError(f"nested bundle has no Info.plist: {bundle}")
            info = plistlib.loads(info_path.read_bytes())
            nested_bundle_id = info.get("CFBundleIdentifier")
            if not nested_bundle_id:
                raise apple_portal.ApplePortalError(f"nested bundle has no CFBundleIdentifier: {bundle}")
            scope = str(bundle.relative_to(app_bundle))
            bundles.append((bundle, str(nested_bundle_id), scope))

        entitlements_by_scope: list[tuple[str, bytes]] = []
        for bundle, identifier, scope in bundles:
            profile = apple_portal.ensure_profile(
                identifier,
                f"{app_name} {bundle.stem}",
                profile_type,
                device_udids=device_udids,
            )
            entitlements_by_scope.append((scope, _profile_entitlements(profile)))
            embedded_profile = bundle / "embedded.mobileprovision"
            embedded_profile.write_bytes(profile)
            embedded_profile.chmod(0o644)

        binary = _rcodesign_binary()
        pem_identity = identity.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ) + identity.certificate.public_bytes(serialization.Encoding.PEM)
        command = [
            binary,
            "sign",
            "--pem-file",
            secret_file_argument(0),
        ]
        secret_values: list[bytes] = [pem_identity]
        for scope, entitlements in entitlements_by_scope:
            secret_values.append(entitlements)
            # rcodesign scoped form is `<scope>:<path>`. The token `main` is a
            # reserved scope name ONLY when it is the entire value (no colon).
            # `main:/path` is parsed as a path-scoped setting for a file named
            # "main", so the main bundle gets no entitlements and iOS install
            # fails with "missing the application-identifier entitlement".
            # Use unscoped for the main bundle; keep path scope for nested ones.
            entitlements_arg = (
                secret_file_argument(len(secret_values) - 1)
                if scope == "main"
                else f"{scope}:{secret_file_argument(len(secret_values) - 1)}"
            )
            command.extend(["--entitlements-xml-file", entitlements_arg])
        command.append(str(app_bundle))
        ret_code, output = exec_cmd(
            command,
            timeout=300,
            secret_files=tuple(secret_values),
        )
        if ret_code != 0:
            raise apple_portal.ApplePortalError(f"rcodesign failed: {output}")
        # Fail closed if the main bundle lost application-identifier (the
        # classic symptom of a broken rcodesign entitlements scope).
        # codesign prints "Executable=..." on stderr; exec_cmd merges streams,
        # so slice strictly from <?xml through </plist>.
        ret_code, output = exec_cmd(
            ["codesign", "-d", "--entitlements", ":-", "--xml", str(app_bundle)],
            timeout=60,
        )
        if ret_code != 0:
            raise apple_portal.ApplePortalError(f"codesign entitlements dump failed: {output}")
        xml_start = output.find("<?xml")
        xml_end = output.rfind("</plist>")
        if xml_start < 0 or xml_end < 0:
            raise apple_portal.ApplePortalError(
                f"codesign entitlements not parseable: {output[:200]}"
            )
        dumped = plistlib.loads(output[xml_start : xml_end + len("</plist>")].encode())
        app_id = dumped.get("application-identifier") if isinstance(dumped, dict) else None
        if not app_id or not str(app_id).startswith(f"{TEAM_ID}."):
            raise apple_portal.ApplePortalError(
                "signed app is missing application-identifier entitlement"
            )
        for bundle, _, _ in bundles:
            info_path = bundle / "Info.plist"
            executable_root = bundle
            if not info_path.is_file():
                info_path = bundle / "Contents" / "Info.plist"
                executable_root = bundle / "Contents" / "MacOS"
            info = plistlib.loads(info_path.read_bytes())
            executable_name = info.get("CFBundleExecutable")
            if not executable_name:
                raise apple_portal.ApplePortalError(f"bundle has no CFBundleExecutable: {bundle}")
            ret_code, output = exec_cmd(
                [binary, "verify", str(executable_root / str(executable_name))],
                timeout=120,
            )
            if ret_code != 0:
                raise apple_portal.ApplePortalError(f"rcodesign verification failed: {output}")
    except (apple_portal.ApplePortalError, OSError, subprocess.SubprocessError) as error:
        print_error(f"Signing failed: {error}")
        return False
    print_success(f"Signed {app_bundle.name} without macOS Keychain")
    return True


def run(project_path: Path, state: ProjectState) -> bool:
    del project_path
    app_name = state.metadata.get("app_name") or state.project_name
    if not ensure_bundle_id(state.bundle_id, app_name):
        return False
    if not ensure_distribution_cert():
        return False
    state.metadata["signing_configured"] = True
    return True
