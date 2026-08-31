# Apple rejection reasons live in Gmail, not the App Store Connect API
from __future__ import annotations

import re
from html.parser import HTMLParser

import psycopg2

GMAIL_ARCHIVE_DSN = "postgres://darrenoakey@127.0.0.1:5432/gmail_archive?sslmode=disable"

_REASON_LINE = re.compile(
    r"(ITMS-\d+|Guideline\s+\d|Please correct|Unsupported SDK|issues with a recent submission|Invalid Binary)",
    re.IGNORECASE,
)


# ##################################################################
# html text extractor
# turns Apple HTML mail into readable lines without style blocks
class HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.ignore_markup = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style"}:
            self.ignore_markup = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.ignore_markup = False
        if tag in {"p", "br", "div", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignore_markup:
            self.parts.append(data)


# ##################################################################
# html to text
# collapse Apple HTML into paragraph text
def html_to_text(document: str) -> str:
    extractor = HtmlTextExtractor()
    extractor.feed(document or "")
    text = re.sub(r"[ \t]+", " ", "".join(extractor.parts))
    return re.sub(r"\n{2,}", "\n", text).strip()


# ##################################################################
# extract rejection text
# keep the ITMS / guideline lines humans need to act on
def extract_rejection_text(body_html: str, body_text: str = "") -> str:
    plain = (body_text or "").strip() or html_to_text(body_html)
    lines = [line.strip() for line in plain.splitlines() if line.strip()]
    matched = [line for line in lines if _REASON_LINE.search(line)]
    chosen = matched or lines
    return "\n".join(chosen[:12]).strip()


# ##################################################################
# lookup rejection
# latest Apple mail for this app name from the local gmail_archive
def lookup_rejection(app_name: str, dsn: str = GMAIL_ARCHIVE_DSN) -> str:
    name = app_name.strip()
    if not name:
        raise ValueError("lookup_rejection requires an app name")
    pattern = f"%{name}%"
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.subject, b.body_html, b.body_text
                FROM messages m
                JOIN message_bodies b USING (gmail_id)
                WHERE m.from_addr ILIKE %s
                  AND (
                    m.subject ILIKE %s
                    OR b.body_html ILIKE %s
                    OR b.body_text ILIKE %s
                  )
                  AND (
                    m.subject ILIKE %s
                    OR m.subject ILIKE %s
                    OR m.subject ILIKE %s
                    OR b.body_html ILIKE %s
                    OR b.body_html ILIKE %s
                    OR b.body_text ILIKE %s
                  )
                ORDER BY m.internal_date DESC
                LIMIT 1
                """,
                (
                    "%apple%",
                    pattern,
                    pattern,
                    pattern,
                    "%Action needed%",
                    "%Invalid Binary%",
                    "%rejected%",
                    "%ITMS-%",
                    "%Guideline%",
                    "%ITMS-%",
                ),
            )
            row = cursor.fetchone()
    if row is None:
        return ""
    subject, body_html, body_text = row
    extracted = extract_rejection_text(body_html or "", body_text or "")
    if extracted:
        return extracted
    return (subject or "").strip()
