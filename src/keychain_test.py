from uuid import uuid4

from keychain import SECURITY, get_password


def test_missing_item_read_uses_macos_security_without_writing() -> None:
    assert SECURITY == "/usr/bin/security"
    assert get_password(f"app-publish-read-check-{uuid4()}", "absent") is None
