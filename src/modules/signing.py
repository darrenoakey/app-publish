"""Provision and apply Apple signatures without using macOS Keychain."""

from pathlib import Path
import json
import plistlib
import re
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


_SIGNING_IDENTITY_ENTITLEMENTS = frozenset(
    {
        "application-identifier",
        "beta-reports-active",
        "com.apple.developer.team-identifier",
        "get-task-allow",
        "keychain-access-groups",
    }
)


_PREFIX_WILDCARD_ENTITLEMENTS = frozenset(
    {
        "application-identifier",
        "com.apple.developer.icloud-container-identifiers",
        "com.apple.developer.ubiquity-container-identifiers",
        "com.apple.developer.ubiquity-kvstore-identifier",
        "keychain-access-groups",
    }
)
_BUILD_VARIABLE = re.compile(r"\$\(([^)]+)\)|\$\{([^}]+)\}")
_PROFILE_VARIABLES = {"AppIdentifierPrefix", "TeamIdentifierPrefix"}


def _profile_prefix(document: dict, name: str) -> str:
    field = "ApplicationIdentifierPrefix" if name == "AppIdentifierPrefix" else "TeamIdentifier"
    values = document.get(field, [])
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str) or not values[0]:
        raise apple_portal.ApplePortalError(f"provisioning profile has no unambiguous {name}")
    return values[0].rstrip(".") + "."


def _resolve_profile_variables(value, document: dict):
    if isinstance(value, list):
        return [_resolve_profile_variables(item, document) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_profile_variables(item, document) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    resolved = value
    for name in _PROFILE_VARIABLES:
        for macro in (f"$({name})", f"${{{name}}}"):
            if macro in resolved:
                resolved = resolved.replace(macro, _profile_prefix(document, name))
    if _BUILD_VARIABLE.search(resolved):
        raise apple_portal.ApplePortalError(f"requested entitlement has an unresolved variable: {value}")
    return resolved


def _value_authorized(key: str, requested, authorized) -> bool:
    if isinstance(requested, list):
        return isinstance(authorized, list) and all(
            any(_value_authorized(key, requested_item, authorized_item) for authorized_item in authorized)
            for requested_item in requested
        )
    if not isinstance(requested, str):
        return type(requested) is type(authorized) and requested == authorized
    if not isinstance(authorized, str):
        return False
    if requested == authorized:
        return True
    if key == "com.apple.developer.associated-domains" and authorized == "*":
        return True
    return (
        key in _PREFIX_WILDCARD_ENTITLEMENTS
        and authorized.endswith("*")
        and "*" not in requested
        and requested.startswith(authorized[:-1])
    )


def _select_entitlements(profile: dict, requested: dict, document: dict | None = None) -> dict:
    resolved = _resolve_profile_variables(requested, document or {})
    unauthorized = sorted(
        key for key, value in resolved.items() if key not in profile or not _value_authorized(key, value, profile[key])
    )
    if unauthorized:
        raise apple_portal.ApplePortalError(
            "provisioning profile does not authorize requested entitlements: " + ", ".join(unauthorized)
        )
    selected = {
        key: value for key, value in profile.items() if key in _SIGNING_IDENTITY_ENTITLEMENTS and key not in resolved
    }
    selected.update(resolved)
    return selected


def _profile_entitlements(profile: bytes, requested: dict | None = None) -> bytes:
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
    selected = _select_entitlements(entitlements, requested or {}, document)
    return plistlib.dumps(selected, fmt=plistlib.FMT_XML)


def _resolve_build_variables(value, settings: dict):
    if isinstance(value, list):
        return [_resolve_build_variables(item, settings) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_build_variables(item, settings) for key, item in value.items()}
    if not isinstance(value, str):
        return value

    def replace(match: re.Match) -> str:
        name = match.group(1) or match.group(2)
        if name in _PROFILE_VARIABLES:
            return match.group(0)
        replacement = settings.get(name)
        if not isinstance(replacement, (str, int, float)):
            raise apple_portal.ApplePortalError(f"Xcode did not resolve entitlement variable {name}: {value}")
        return str(replacement)

    return _BUILD_VARIABLE.sub(replace, value)


def _read_requested_entitlements(settings: dict, source_root: Path) -> dict:
    entitlements_setting = settings.get("CODE_SIGN_ENTITLEMENTS", "").strip()
    if not entitlements_setting:
        return {}
    target_root = Path(settings.get("SRCROOT") or settings.get("PROJECT_DIR") or source_root)
    if not target_root.is_absolute():
        target_root = source_root / target_root
    expanded = entitlements_setting.replace("$(SRCROOT)", str(target_root)).replace("${SRCROOT}", str(target_root))
    expanded = expanded.replace("$(PROJECT_DIR)", str(target_root)).replace("${PROJECT_DIR}", str(target_root))
    entitlements_path = Path(expanded)
    if not entitlements_path.is_absolute():
        entitlements_path = target_root / entitlements_path
    try:
        requested = plistlib.loads(entitlements_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as error:
        raise apple_portal.ApplePortalError(
            f"could not read requested entitlements from {entitlements_path}: {error}"
        ) from error
    if not isinstance(requested, dict):
        raise apple_portal.ApplePortalError(f"requested entitlements are not a dictionary: {entitlements_path}")
    return _resolve_build_variables(requested, settings)


def _decode_build_settings(output: str) -> list:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\[", output):
        try:
            documents, end = decoder.raw_decode(output[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(documents, list) and not output[match.start() + end :].strip():
            return documents
    raise apple_portal.ApplePortalError("xcodebuild returned unreadable build settings")


def requested_entitlements(
    xcode_project: str,
    scheme: str,
    configuration: str,
    *,
    use_workspace: bool,
) -> dict[str, dict]:
    command = ["xcodebuild", "-workspace" if use_workspace else "-project", xcode_project]
    if use_workspace:
        command.extend(["-scheme", scheme])
    else:
        command.append("-alltargets")
    command.extend(
        [
            "-configuration",
            configuration,
            "-showBuildSettings",
            "-json",
        ]
    )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise apple_portal.ApplePortalError(f"could not resolve source entitlements: {detail}")
    documents = _decode_build_settings(completed.stdout)
    source_root = Path(xcode_project).resolve().parent
    requested_by_bundle: dict[str, dict] = {}
    for item in documents:
        settings = item.get("buildSettings", {})
        identifier = settings.get("PRODUCT_BUNDLE_IDENTIFIER")
        if not identifier:
            continue
        requested = _read_requested_entitlements(settings, source_root)
        if identifier in requested_by_bundle and requested_by_bundle[identifier] != requested:
            raise apple_portal.ApplePortalError(f"conflicting Xcode entitlements for bundle identifier {identifier}")
        requested_by_bundle[identifier] = requested
    if not requested_by_bundle:
        raise apple_portal.ApplePortalError("Xcode returned no targets with bundle identifiers")
    return requested_by_bundle


def sign_app_bundle(
    app_bundle: Path,
    bundle_id: str,
    app_name: str,
    profile_type: str,
    *,
    device_udids: list[str] | None = None,
    requested: dict[str, dict] | None = None,
) -> bool:
    """Embed a current profile and recursively sign with an in-memory P12."""
    try:
        if requested is None:
            raise apple_portal.ApplePortalError("per-bundle requested entitlements are required")
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
            if identifier not in requested:
                raise apple_portal.ApplePortalError(f"no requested entitlements resolved for bundle {identifier}")
            profile = apple_portal.ensure_profile(
                identifier,
                f"{app_name} {bundle.stem}",
                profile_type,
                device_udids=device_udids,
            )
            entitlements_by_scope.append((scope, _profile_entitlements(profile, requested[identifier])))
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
            raise apple_portal.ApplePortalError(f"codesign entitlements not parseable: {output[:200]}")
        dumped = plistlib.loads(output[xml_start : xml_end + len("</plist>")].encode())
        app_id = dumped.get("application-identifier") if isinstance(dumped, dict) else None
        if not app_id or not str(app_id).startswith(f"{TEAM_ID}."):
            raise apple_portal.ApplePortalError("signed app is missing application-identifier entitlement")
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
