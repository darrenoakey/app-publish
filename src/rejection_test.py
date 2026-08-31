from rejection import extract_rejection_text, lookup_rejection

GOLF_HTML = """
<html><head><style>A { color: red; }</style></head><body>
<p>Hello,</p>
<p>We noticed one or more issues with a recent submission for App Store review for the following app:</p>
<p>OL Golf</p>
<p>Please correct the following issues and upload a new binary to App Store Connect.</p>
<p>ITMS-90111: Unsupported SDK or Xcode version - App submissions must use the latest Xcode and SDK Release Candidates (RC).</p>
</body></html>
"""


def test_extract_rejection_text_keeps_itms_lines() -> None:
    text = extract_rejection_text(GOLF_HTML, "")
    assert "ITMS-90111: Unsupported SDK or Xcode version" in text
    assert "Please correct the following issues" in text
    assert "color: red" not in text


def test_lookup_rejection_reads_gmail_archive_for_ol_golf() -> None:
    text = lookup_rejection("OL Golf")
    assert "ITMS-90111" in text or "Invalid Binary" in text
