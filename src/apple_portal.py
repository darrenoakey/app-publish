"""App Store Connect provisioning without macOS Keychain access."""

from __future__ import annotations

import base64
import datetime as dt
from dataclasses import dataclass

import jwt
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12

import secrets_store

BASE_URL = "https://api.appstoreconnect.apple.com/v1"
SERVICE = "app-publish"
P12_ACCOUNT = "apple_distribution_p12"
RENEWAL_WINDOW = dt.timedelta(days=30)


class ApplePortalError(RuntimeError):
    """A provisioning request failed or returned inconsistent data."""


@dataclass(frozen=True)
class SigningIdentity:
    p12: bytes
    certificate_id: str
    certificate: x509.Certificate
    private_key: object


def _required_text(account: str) -> str:
    value = secrets_store.get_secret(SERVICE, account)
    if not value:
        raise ApplePortalError(f"missing daz-secrets account: {SERVICE}/{account}")
    return value.strip()


_cached_token: tuple[int, str] = (0, "")


def _token() -> str:
    global _cached_token
    now = int(dt.datetime.now(tz=dt.UTC).timestamp())
    exp, token = _cached_token
    if exp > now + 60 and token:
        return token
    new_exp = now + 10 * 60
    new_token = jwt.encode(
        {
            "iss": _required_text("api_issuer_id"),
            "iat": now,
            "exp": new_exp,
            "aud": "appstoreconnect-v1",
        },
        _required_text("api_private_key"),
        algorithm="ES256",
        headers={"alg": "ES256", "kid": _required_text("api_key_id"), "typ": "JWT"},
    )
    _cached_token = (new_exp, new_token)
    return new_token


def request(
    method: str,
    path: str,
    *,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
) -> dict:
    response = requests.request(
        method,
        f"{BASE_URL}/{path.lstrip('/')}",
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        params=params,
        json=json,
        timeout=30,
    )
    if not response.ok:
        try:
            body = response.json()
            errors = body.get("errors", [])
            detail = "; ".join(str(item.get("detail") or item.get("title")) for item in errors)
        except (ValueError, AttributeError):
            detail = response.text[:500]
        raise ApplePortalError(f"App Store Connect {method} {path} failed ({response.status_code}): {detail}")
    return response.json()


def ensure_bundle_id(identifier: str, name: str) -> str:
    document = request(
        "GET",
        "bundleIds",
        params={"filter[identifier]": identifier, "limit": 2},
    )
    matches = document.get("data", [])
    if matches:
        return str(matches[0]["id"])
    created = request(
        "POST",
        "bundleIds",
        json={
            "data": {
                "type": "bundleIds",
                "attributes": {"name": name, "identifier": identifier, "platform": "IOS"},
            }
        },
    )
    return str(created["data"]["id"])


def _load_p12(value: bytes) -> tuple[object, x509.Certificate]:
    try:
        private_key, certificate, _ = pkcs12.load_key_and_certificates(value, None)
    except ValueError as error:
        raise ApplePortalError("stored Apple Distribution P12 is invalid or password-protected") from error
    if private_key is None or certificate is None:
        raise ApplePortalError("stored Apple Distribution P12 lacks a private key or certificate")
    return private_key, certificate


def _certificate_expiry(certificate: x509.Certificate) -> dt.datetime:
    expiry = getattr(certificate, "not_valid_after_utc", None)
    if expiry is not None:
        return expiry
    return certificate.not_valid_after.replace(tzinfo=dt.UTC)


def _remote_certificate_id(certificate: x509.Certificate) -> str | None:
    serial = format(certificate.serial_number, "X")
    document = request(
        "GET",
        "certificates",
        params={"filter[serialNumber]": serial, "limit": 2},
    )
    for item in document.get("data", []):
        certificate_type = item.get("attributes", {}).get("certificateType")
        if certificate_type in {"DISTRIBUTION", "IOS_DISTRIBUTION"}:
            return str(item["id"])
    return None


def _create_distribution_identity() -> SigningIdentity:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "app-publish automated signing")])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(private_key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode("ascii")
    )
    document = request(
        "POST",
        "certificates",
        json={
            "data": {
                "type": "certificates",
                "attributes": {
                    "certificateType": "DISTRIBUTION",
                    "csrContent": csr,
                },
            }
        },
    )
    data = document["data"]
    certificate = x509.load_der_x509_certificate(base64.b64decode(data["attributes"]["certificateContent"]))
    value = pkcs12.serialize_key_and_certificates(
        b"Apple Distribution",
        private_key,
        certificate,
        None,
        serialization.NoEncryption(),
    )
    secrets_store.set_secret_bytes(SERVICE, P12_ACCOUNT, value)
    if secrets_store.get_secret_bytes(SERVICE, P12_ACCOUNT) != value:
        raise ApplePortalError("new Apple Distribution P12 failed encrypted-store readback verification")
    return SigningIdentity(value, str(data["id"]), certificate, private_key)


def ensure_distribution_identity() -> SigningIdentity:
    value = secrets_store.get_secret_bytes(SERVICE, P12_ACCOUNT)
    if value:
        private_key, certificate = _load_p12(value)
        certificate_id = _remote_certificate_id(certificate)
        now = dt.datetime.now(tz=dt.UTC)
        if certificate_id and _certificate_expiry(certificate) > now + RENEWAL_WINDOW:
            return SigningIdentity(value, certificate_id, certificate, private_key)
    return _create_distribution_identity()


def _profile_certificate_ids(profile_id: str) -> set[str]:
    document = request("GET", f"profiles/{profile_id}/relationships/certificates")
    return {str(item["id"]) for item in document.get("data", [])}


def _profile_is_current(item: dict, certificate_id: str, profile_type: str) -> bool:
    attributes = item.get("attributes", {})
    if attributes.get("profileState") != "ACTIVE" or attributes.get("profileType") != profile_type:
        return False
    expiration = dt.datetime.fromisoformat(str(attributes["expirationDate"]).replace("Z", "+00:00"))
    if expiration <= dt.datetime.now(tz=dt.UTC) + RENEWAL_WINDOW:
        return False
    return certificate_id in _profile_certificate_ids(str(item["id"]))


def _device_relationship(udids: list[str]) -> list[dict[str, str]]:
    if not udids:
        return []
    document = request("GET", "devices", params={"limit": 200})
    wanted = {value.upper() for value in udids}
    found = [
        {"type": "devices", "id": str(item["id"])}
        for item in document.get("data", [])
        if str(item.get("attributes", {}).get("udid", "")).upper() in wanted
        and item.get("attributes", {}).get("status") == "ENABLED"
    ]
    if len(found) != len(wanted):
        raise ApplePortalError("one or more target devices are not registered in the Apple Developer portal")
    return found


def ensure_profile(
    bundle_identifier: str,
    app_name: str,
    profile_type: str,
    *,
    device_udids: list[str] | None = None,
) -> bytes:
    identity = ensure_distribution_identity()
    bundle_id = ensure_bundle_id(bundle_identifier, app_name)
    profiles = request(
        "GET",
        f"bundleIds/{bundle_id}/profiles",
        params={"limit": 200},
    ).get("data", [])
    for item in profiles:
        if _profile_is_current(item, identity.certificate_id, profile_type):
            content = item.get("attributes", {}).get("profileContent")
            if content:
                return base64.b64decode(content)
            profile = request("GET", f"profiles/{item['id']}")
            return base64.b64decode(profile["data"]["attributes"]["profileContent"])

    relationships: dict[str, object] = {
        "bundleId": {"data": {"type": "bundleIds", "id": bundle_id}},
        "certificates": {"data": [{"type": "certificates", "id": identity.certificate_id}]},
    }
    if profile_type == "IOS_APP_ADHOC":
        relationships["devices"] = {"data": _device_relationship(device_udids or [])}
    created = request(
        "POST",
        "profiles",
        json={
            "data": {
                "type": "profiles",
                "attributes": {
                    "name": f"app-publish {profile_type} {bundle_identifier}",
                    "profileType": profile_type,
                },
                "relationships": relationships,
            }
        },
    )
    return base64.b64decode(created["data"]["attributes"]["profileContent"])
