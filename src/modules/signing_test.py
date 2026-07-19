import importlib

import modules.signing as signing_module
from modules.signing import (
    ensure_bundle_id,
    ensure_distribution_cert,
    has_distribution_cert,
)

importlib.reload(signing_module)


def test_distribution_identity_query_returns_real_boolean() -> None:
    assert isinstance(has_distribution_cert(), bool)


def test_signing_commands_fail_cleanly_when_real_binaries_are_absent(tmp_path, capsys) -> None:
    absent = str(tmp_path / "not-installed")
    assert has_distribution_cert(absent) is False
    assert ensure_bundle_id("com.example.reader", "Reader", absent) is False
    assert ensure_distribution_cert(absent, absent) is False
    output = capsys.readouterr().out
    assert "Bundle ID check failed" in output
    assert "cert action failed" in output
