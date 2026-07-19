import importlib

import modules.support as support_module
from modules.support import (
    generate_404_html,
    generate_index_html,
    generate_privacy_policy_html,
    generate_support_html,
    invalidate_cloudfront,
    list_apps_from_s3,
    run,
    upload_string_to_s3,
    upload_to_s3,
)
from state import ProjectState

importlib.reload(support_module)


def test_support_pages_escape_user_content_and_build_links() -> None:
    page = generate_support_html("A&B <App>", "12345", subtitle='Use "safely"')
    assert "A&amp;B &lt;App&gt;" in page
    assert "https://apps.apple.com/app/id12345" in page
    assert "<script>A&B <App></script>" not in page
    index = generate_index_html([{"slug": "safe-app", "name": "Safe & Sound"}])
    assert 'href="/safe-app/"' in index
    assert "Safe &amp; Sound" in index
    assert "<h1>404</h1>" in generate_404_html()
    assert "No apps published yet" in generate_index_html([])
    privacy = generate_privacy_policy_html("A&B <App>", "Local app")
    assert "Privacy Policy for A&amp;B &lt;App&gt;" in privacy
    assert "<script>A&B <App></script>" not in privacy


def test_cloud_boundaries_fail_cleanly_with_a_real_missing_local_binary(tmp_path, capsys) -> None:
    absent = str(tmp_path / "not-installed")
    page = tmp_path / "index.html"
    page.write_text("<h1>Support</h1>")
    assert list_apps_from_s3(absent) == []
    assert upload_to_s3(page, "reader/index.html", aws_binary=absent) is False
    assert upload_string_to_s3("<h1>Support</h1>", "reader/index.html", aws_binary=absent) is False
    assert invalidate_cloudfront(None, absent) is False
    assert run(tmp_path, ProjectState()) is True
    output = capsys.readouterr().out
    assert "Could not list apps from S3" in output
    assert "S3 upload failed" in output
    assert "CloudFront invalidation failed" in output
