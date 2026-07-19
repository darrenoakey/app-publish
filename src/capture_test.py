import sys

from capture import capture, get_screens, main
from state import ProjectState, save_state


def test_capture_rejects_unknown_screen_without_creating_output(tmp_path, capsys) -> None:
    assert not capture(tmp_path, "missing", {"main": {"description": "Main"}})
    assert "Available: main" in capsys.readouterr().out
    assert not (tmp_path / "fastlane").exists()


def test_cached_screens_and_cli_validation(tmp_path, capsys) -> None:
    scenarios = [{"name": "main", "description": "Main", "navigation": "Launch"}]
    state = ProjectState(project_path=str(tmp_path), project_name=tmp_path.name)
    state.metadata["screenshot_scenarios"] = scenarios
    save_state(tmp_path, state)
    assert get_screens(tmp_path) == {"main": scenarios[0]}

    original = sys.argv
    try:
        sys.argv = ["capture.py"]
        assert main() == 1
        sys.argv = ["capture.py", str(tmp_path / "absent")]
        assert main() == 1
        sys.argv = ["capture.py", str(tmp_path)]
        assert main() == 0
        assert "Available screens" in capsys.readouterr().out
        sys.argv = ["capture.py", str(tmp_path), "unknown"]
        assert main() == 1
    finally:
        sys.argv = original
