"""Real App Store Connect coverage for the machine-local provisioning client."""

import base64
import datetime as dt
import plistlib
import uuid

import pytest
from cryptography import x509

import apple_portal
from modules.signing import _profile_entitlements


def test_real_apple_api_profile_certificate_and_device_flow() -> None:
    bundles = apple_portal.request(
        "GET",
        "bundleIds",
        params={"filter[identifier]": "com.darrenoakey.olBridge", "limit": 2},
    )
    assert len(bundles["data"]) == 1
    bundle = bundles["data"][0]
    bundle_id = bundle["id"]
    assert (
        apple_portal.ensure_bundle_id(
            bundle["attributes"]["identifier"],
            bundle["attributes"]["name"],
        )
        == bundle_id
    )

    profiles = apple_portal.request(
        "GET",
        f"bundleIds/{bundle_id}/profiles",
        params={"limit": 20},
    )["data"]
    assert profiles
    profile = apple_portal.request(
        "GET",
        f"profiles/{profiles[0]['id']}",
    )
    content = base64.b64decode(profile["data"]["attributes"]["profileContent"])
    entitlements = plistlib.loads(_profile_entitlements(content))
    assert entitlements["com.apple.developer.team-identifier"]

    app_store_profile = next(item for item in profiles if item["attributes"]["profileType"] == "IOS_APP_STORE")
    profile_certificate_ids = apple_portal._profile_certificate_ids(app_store_profile["id"])
    assert profile_certificate_ids
    assert apple_portal._profile_is_current(
        app_store_profile,
        next(iter(profile_certificate_ids)),
        "IOS_APP_STORE",
    )

    certificates = apple_portal.request(
        "GET",
        "certificates",
        params={"filter[certificateType]": "DISTRIBUTION", "limit": 20},
    )["data"]
    assert certificates
    certificate_document = apple_portal.request(
        "GET",
        f"certificates/{certificates[0]['id']}",
    )
    certificate = x509.load_der_x509_certificate(
        base64.b64decode(certificate_document["data"]["attributes"]["certificateContent"])
    )
    assert apple_portal._remote_certificate_id(certificate) == certificates[0]["id"]
    assert apple_portal._certificate_expiry(certificate) > dt.datetime.now(tz=dt.UTC)

    devices = apple_portal._device_relationship(["00008150-000611360AC0401C"])
    assert len(devices) == 1
    assert devices[0]["type"] == "devices"


def test_listed_but_inaccessible_profile_is_stale() -> None:
    # Apple can list an ACTIVE profile whose certificate relationship 404s; the
    # original ghost profile has since been purged, so a vanished id reproduces
    # the same live 404 against the real API.
    ghost_profile = {
        "id": f"ZZ{uuid.uuid4().hex[:8].upper()}",
        "attributes": {
            "profileState": "ACTIVE",
            "profileType": "IOS_APP_STORE",
            "expirationDate": (dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=300)).isoformat(),
        },
    }
    with pytest.raises(apple_portal.ApplePortalError) as exc_info:
        apple_portal._profile_certificate_ids(ghost_profile["id"])
    assert exc_info.value.status_code == 404
    assert not apple_portal._profile_is_current(ghost_profile, "unresolvable-certificate", "IOS_APP_STORE")


def test_invalid_local_and_remote_material_fails_closed() -> None:
    with pytest.raises(apple_portal.ApplePortalError):
        apple_portal._required_text("does-not-exist")
    with pytest.raises(apple_portal.ApplePortalError):
        apple_portal._load_p12(b"not a p12")
    with pytest.raises(apple_portal.ApplePortalError):
        apple_portal._device_relationship(["NOT-A-REGISTERED-DEVICE"])
    with pytest.raises(apple_portal.ApplePortalError) as exc_info:
        apple_portal.request("GET", "definitely-not-an-apple-resource")
    assert exc_info.value.status_code == 404
