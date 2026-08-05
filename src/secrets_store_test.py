from uuid import uuid4

from secrets_store import delete_secret, get_secret, get_secret_bytes, set_secret


def test_real_provider_round_trip_is_prompt_free() -> None:
    service = f"app-publish-test-{uuid4()}"
    account = "round-trip"
    assert get_secret(service, account) is None
    set_secret(service, account, "non-production-test-value")
    try:
        assert get_secret(service, account) == "non-production-test-value"
        assert get_secret_bytes(service, account) == b"non-production-test-value"
    finally:
        assert delete_secret(service, account)
    assert get_secret(service, account) is None
