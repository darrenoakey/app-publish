import sys

from app_publish import (
    compute_www_hash,
    increment_version,
    main,
    run_pipeline,
    run_step,
)
from config import PIPELINE_STEPS
from state import ProjectState, load_state, save_state


def test_www_hash_tracks_paths_and_content_deterministically(tmp_path) -> None:
    assert compute_www_hash(tmp_path) == ""
    www = tmp_path / "www"
    (www / "nested").mkdir(parents=True)
    (www / "index.html").write_text("alpha")
    (www / "nested" / "app.js").write_text("beta")
    first = compute_www_hash(tmp_path)
    assert first == compute_www_hash(tmp_path)
    (www / "nested" / "app.js").write_text("gamma")
    assert compute_www_hash(tmp_path) != first
    assert increment_version("7.4.3") == "8"


def test_detect_step_and_resumable_pipeline_complete_locally(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<main>reader</main>")
    state = ProjectState(project_path=str(tmp_path), project_name=tmp_path.name)
    assert not run_step("not-a-step", tmp_path, state)
    assert run_step("detect", tmp_path, state)
    persisted = load_state(tmp_path)
    assert persisted.project_type == "web"
    assert "detect" in persisted.completed_steps

    persisted.completed_steps = [step for step in PIPELINE_STEPS if step != "detect"]
    persisted.current_step = ""
    persisted.metadata["www_hash"] = ""
    save_state(tmp_path, persisted)
    assert run_pipeline(tmp_path)
    assert load_state(tmp_path).is_complete()


def test_cli_validates_paths_and_reads_real_state_file(tmp_path) -> None:
    state = ProjectState(
        project_path=str(tmp_path),
        project_name=tmp_path.name,
        bundle_id="com.example.reader",
    )
    state.mark_step_completed("detect")
    save_state(tmp_path, state)
    original = sys.argv
    try:
        sys.argv = ["app_publish.py", str(tmp_path / "absent")]
        assert main() == 1
        regular_file = tmp_path / "file.txt"
        regular_file.write_text("content")
        sys.argv = ["app_publish.py", str(regular_file)]
        assert main() == 1
        sys.argv = ["app_publish.py", str(tmp_path), "--status"]
        assert main() == 0
    finally:
        sys.argv = original
