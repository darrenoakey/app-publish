"""Prompt-free app-publish credentials through the configured daz-secrets provider."""

from daz_secrets import Client, DazSecretsError, ErrorCode


def get_secret_bytes(service: str, account: str) -> bytes | None:
    """Return exact secret bytes, or ``None`` when the account is absent."""
    try:
        value = Client().get(service, account).value
    except DazSecretsError as error:
        if error.code is ErrorCode.NOT_FOUND:
            return None
        raise
    return value or None


def get_secret(service: str, account: str) -> str | None:
    """Return one UTF-8 secret, or ``None`` when the account is absent."""
    value = get_secret_bytes(service, account)
    return value.decode("utf-8") if value else None


def set_secret(service: str, account: str, value: str) -> None:
    """Persist one UTF-8 secret without placing it in argv or an environment variable."""
    Client().set(service, account, value.encode("utf-8"))


def set_secret_bytes(service: str, account: str, value: bytes) -> None:
    """Persist exact secret bytes without passing them through text encoding."""
    Client().set(service, account, value)


def delete_secret(service: str, account: str) -> bool:
    """Delete one secret and report whether it existed."""
    return Client().delete(service, account).deleted
