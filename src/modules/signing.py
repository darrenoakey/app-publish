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

# `xcodebuild -showBuildSettings` only reads project metadata, but it still
# resolves Swift package manifests and can take minutes on a loaded host. The
# old 60s ceiling failed real deploys with an unhandled TimeoutExpired, so keep
# the ceiling generous and report overruns as a retryable portal error.
BUILD_SETTINGS_TIMEOUT = 600


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
        "aps-environment",
        "beta-reports-active",
        "com.apple.developer.team-identifier",
        "get-task-allow",
        "keychain-access-groups",
    }
)

# Entitlements whose value is decided by the provisioning profile, not by the
# app's own entitlements file. A distribution/ad-hoc profile grants
# aps-environment "production" while a Debug build asks for "development";
# requiring them to be equal rejected every push-enabled app. Xcode signs with
# the profile's value, so drop the requested one and carry the profile's.
_PROFILE_AUTHORITATIVE_ENTITLEMENTS = frozenset({"aps-environment"})


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


class ExactIdentityError(RuntimeError):
    """A profile cannot safely represent this concrete target; never re-mint it."""


def _exact_identity_error(message: str):
    error = ExactIdentityError(message)
    error.__cause__ = apple_portal.ApplePortalError(message)
    raise error


def _profile_team(entitlements: dict, document: dict, bundle_id: str) -> tuple[str, str]:
    """Bind a signature to its exact target and the profile's actual team."""
    prefix_values = document.get("ApplicationIdentifierPrefix", [])
    if not isinstance(prefix_values, list) or len(prefix_values) != 1:
        _exact_identity_error("provisioning profile has no unambiguous AppIdentifierPrefix")
    app_prefix = prefix_values[0]
    team = entitlements.get("com.apple.developer.team-identifier")
    if not isinstance(app_prefix, str) or not app_prefix or not isinstance(team, str) or not team:
        _exact_identity_error("provisioning profile has no concrete team entitlement")
    if app_prefix != team:
        _exact_identity_error("profile AppIdentifierPrefix and team entitlement differ")
    expected = f"{app_prefix}.{bundle_id}"
    application_id = entitlements.get("application-identifier")
    if application_id != expected:
        _exact_identity_error(
            f"profile application-identifier {application_id!r} does not match exact target {expected!r}"
        )
    return expected, team


def _exact_signature_entitlements(profile: dict, resolved: dict, document: dict, bundle_id: str) -> dict:
    expected, team = _profile_team(profile, document, bundle_id)
    selected = {
        key: value for key, value in profile.items() if key in _SIGNING_IDENTITY_ENTITLEMENTS and key not in resolved
    }
    selected.update(resolved)
    requested_id = resolved.get("application-identifier", expected)
    requested_team = resolved.get("com.apple.developer.team-identifier", team)
    if requested_id != expected or requested_team != team:
        _exact_identity_error("requested app or team entitlement does not match the exact target profile")
    selected["application-identifier"] = expected
    selected["com.apple.developer.team-identifier"] = team
    groups = resolved.get("keychain-access-groups")
    if groups is None:
        groups = [expected]
    elif not isinstance(groups, list) or not groups or not all(isinstance(group, str) for group in groups):
        _exact_identity_error("keychain-access-groups must contain concrete strings")
    elif any("*" in group or group == "com.apple.token" for group in groups):
        _exact_identity_error("wildcard or unsupported token keychain group cannot be signed")
    elif any(group != expected for group in groups):
        _exact_identity_error("requested keychain group is not the exact target app group")
    if not _value_authorized("keychain-access-groups", groups, profile.get("keychain-access-groups")):
        _exact_identity_error("profile does not authorize exact target keychain group")
    selected["keychain-access-groups"] = groups
    return selected


def _select_entitlements(
    profile: dict, requested: dict, document: dict | None = None, bundle_id: str | None = None
) -> dict:
    resolved = _resolve_profile_variables(requested, document or {})
    # These are decided by the profile, so only require that it grants them at
    # all; the profile's own value is carried into the signature below.
    for key in _PROFILE_AUTHORITATIVE_ENTITLEMENTS:
        resolved.pop(key, None)
    unauthorized = sorted(
        key for key, value in resolved.items() if key not in profile or not _value_authorized(key, value, profile[key])
    )
    unauthorized.extend(
        sorted(key for key in _PROFILE_AUTHORITATIVE_ENTITLEMENTS if key in requested and key not in profile)
    )
    if unauthorized:
        raise apple_portal.ApplePortalError(
            "provisioning profile does not authorize requested entitlements: " + ", ".join(unauthorized)
        )
    selected = {
        key: value for key, value in profile.items() if key in _SIGNING_IDENTITY_ENTITLEMENTS and key not in resolved
    }
    selected.update(resolved)
    if bundle_id is not None:
        return _exact_signature_entitlements(profile, resolved, document or {}, bundle_id)
    return selected


def _profile_entitlements(profile: bytes, requested: dict | None = None, bundle_id: str | None = None) -> bytes:
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
    selected = _select_entitlements(entitlements, requested or {}, document, bundle_id)
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
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=BUILD_SETTINGS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        # A loaded host can make this metadata-only query take minutes; report it
        # as a retryable portal error instead of an unhandled traceback.
        raise apple_portal.ApplePortalError(
            f"xcodebuild -showBuildSettings for {xcode_project} exceeded "
            f"{BUILD_SETTINGS_TIMEOUT}s; the host is overloaded, retry the deploy"
        ) from error
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

        info_path = app_bundle / "Info.plist"
        if not info_path.is_file():
            raise apple_portal.ApplePortalError(f"app bundle has no Info.plist: {app_bundle}")
        actual_identifier = plistlib.loads(info_path.read_bytes()).get("CFBundleIdentifier")
        if actual_identifier != bundle_id:
            raise apple_portal.ApplePortalError(
                f"app package identifier {actual_identifier!r} does not match requested target {bundle_id!r}"
            )

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
            try:
                resolved = _profile_entitlements(profile, requested[identifier], identifier)
            except ExactIdentityError:
                raise
            except apple_portal.ApplePortalError:
                # A reused profile can predate a capability the App ID now has
                # (for example PUSH_NOTIFICATIONS), so it cannot authorize what
                # the app declares. Re-mint it once, then let a second failure
                # surface as the real error.
                profile = apple_portal.ensure_profile(
                    identifier,
                    f"{app_name} {bundle.stem}",
                    profile_type,
                    device_udids=device_udids,
                    force=True,
                )
                resolved = _profile_entitlements(profile, requested[identifier], identifier)
            entitlements_by_scope.append((scope, resolved))
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
        if app_id != f"{TEAM_ID}.{bundle_id}":
            raise apple_portal.ApplePortalError("signed app application-identifier does not exactly match its target")
        if dumped.get("com.apple.developer.team-identifier") != TEAM_ID:
            raise apple_portal.ApplePortalError("signed app team entitlement does not match the configured team")
        groups = dumped.get("keychain-access-groups")
        if (
            not isinstance(groups, list)
            or not groups
            or any(not isinstance(group, str) or "*" in group or group == "com.apple.token" for group in groups)
        ):
            raise apple_portal.ApplePortalError("signed app has wildcard or unsupported keychain groups")
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
    except (apple_portal.ApplePortalError, ExactIdentityError, OSError, subprocess.SubprocessError) as error:
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
