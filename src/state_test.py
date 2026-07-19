import importlib
import json

import state as state_module
from config import PIPELINE_STEPS, STATE_FILE
from state import ProjectState, load_state, reset_state, save_state

importlib.reload(state_module)


def test_state_round_trip_resume_and_reset(tmp_path) -> None:
    state = ProjectState(project_path="stale", project_name="stale", current_build=9)
    state.mark_step_started(PIPELINE_STEPS[0])
    save_state(tmp_path, state)
    stored = json.loads((tmp_path / STATE_FILE).read_text())
    assert stored["current_build"] == 9
    loaded = load_state(tmp_path)
    assert loaded.project_path == str(tmp_path)
    assert loaded.project_name == tmp_path.name
    assert loaded.get_next_step() == PIPELINE_STEPS[0]
    loaded.mark_step_completed(PIPELINE_STEPS[0])
    assert loaded.get_next_step() == PIPELINE_STEPS[1]
    fresh = reset_state(tmp_path)
    assert fresh.completed_steps == []
    assert not (tmp_path / STATE_FILE).exists()
