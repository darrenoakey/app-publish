# durable Beezle notices via waggler's notification outbox
from __future__ import annotations

import psycopg2


PRODUCTION_NOTICE_DSN = "postgres://darrenoakey@127.0.0.1:5432/beezle3?sslmode=disable"
TEST_NOTICE_DSN = "postgres://darrenoakey@127.0.0.1:5432/beezle3_test?sslmode=disable"


# ##################################################################
# notice store
# append-only outbox used by Beezle
class NoticeStore:
    def notify(self, source: str, key: str, message: str) -> None:
        raise NotImplementedError


# postgres notice store
# writes the same unique (source, key) row waggler uses so Beezle can speak it
class PostgresNoticeStore(NoticeStore):
    def __init__(self, dsn: str = PRODUCTION_NOTICE_DSN) -> None:
        self.dsn = dsn

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
