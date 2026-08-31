# durable Beezle notices via waggler's notification outbox
from __future__ import annotations

import psycopg2

NOTICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS waggler_notifications (
    seq BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, idempotency_key)
)
"""

PRODUCTION_NOTICE_DSN = "postgres://darrenoakey@127.0.0.1:5432/beezle3?sslmode=disable"
TEST_NOTICE_DSN = "postgres://darrenoakey@127.0.0.1:5432/beezle3_test?sslmode=disable"


# ##################################################################
# notice store
# append-only outbox used by Beezle; two real implementations share this contract
class NoticeStore:
    def notify(self, source: str, key: str, message: str) -> None:
        raise NotImplementedError


# ##################################################################
# recorded notice store
# keeps rows in process for tests that must not touch the Beezle database
class RecordedNoticeStore(NoticeStore):
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def notify(self, source: str, key: str, message: str) -> None:
        source, key, message = _require_notice(source, key, message)
        for existing_source, existing_key, _message in self.rows:
            if existing_source == source and existing_key == key:
                return
        self.rows.append((source, key, message))


# ##################################################################
# postgres notice store
# writes the same unique (source, key) row waggler uses so Beezle can speak it
class PostgresNoticeStore(NoticeStore):
    def __init__(self, dsn: str = PRODUCTION_NOTICE_DSN) -> None:
        self.dsn = dsn
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(NOTICE_SCHEMA)
            connection.commit()

    def notify(self, source: str, key: str, message: str) -> None:
        source, key, message = _require_notice(source, key, message)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO waggler_notifications (source, idempotency_key, message)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (source, idempotency_key) DO NOTHING
                    """,
                    (source, key, message),
                )
            connection.commit()


# ##################################################################
# require notice
# empty source, key, or message is not a notice
def _require_notice(source: str, key: str, message: str) -> tuple[str, str, str]:
    source = source.strip()
    key = key.strip()
    message = message.strip()
    if not source or not key or not message:
        raise ValueError("notification requires a source, idempotency key, and message")
    return source, key, message
