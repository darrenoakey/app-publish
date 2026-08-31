import uuid

import psycopg2
import pytest

from notices import PostgresNoticeStore, TEST_NOTICE_DSN, _require_notice


def test_postgres_notice_store_inserts_once_per_source_and_key() -> None:
    store = PostgresNoticeStore(TEST_NOTICE_DSN)
    source = "app-publish-test"
    key = f"notice-{uuid.uuid4()}"
    store.notify(source, key, "OL Golf REJECTED")
    store.notify(source, key, "OL Golf REJECTED again")
    with psycopg2.connect(TEST_NOTICE_DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT message FROM waggler_notifications
                WHERE source = %s AND idempotency_key = %s
                """,
                (source, key),
            )
            rows = cursor.fetchall()
            cursor.execute(
                "DELETE FROM waggler_notifications WHERE source = %s AND idempotency_key = %s",
                (source, key),
            )
        connection.commit()
    assert rows == [("OL Golf REJECTED",)]


def test_notice_requires_source_key_and_message() -> None:
    with pytest.raises(ValueError):
        _require_notice(" ", "key", "message")
    with pytest.raises(ValueError):
        _require_notice("source", " ", "message")
    with pytest.raises(ValueError):
        _require_notice("source", "key", " ")
