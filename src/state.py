# state management for app-publish
# handles loading/saving project state, tracking completed steps for
# idempotency, and resumability after failures
import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field, asdict

from config import STATE_FILE, PIPELINE_STEPS


# ##################################################################
# project state
# represents the current state of a project's publish pipeline
@dataclass
class ProjectState:
    # project identification
    project_path: str = ""
    project_type: str = ""  # "web" or "swift"
    project_name: str = ""  # directory name

    # app identity
    bundle_id: str = ""
    app_name: str = ""
    app_subtitle: str = ""
    app_description: str = ""
    app_keywords: list[str] = field(default_factory=list)

    # app store connect
    app_store_id: str = ""  # apple's app id once created

    # versioning
    current_version: str = "1.0"
    current_build: int = 0

    # pipeline state
    completed_steps: list[str] = field(default_factory=list)
    current_step: str = ""
    last_error: str = ""

    # timestamps
    created_at: str = ""
    last_run: str = ""

    # additional metadata
    metadata: dict[str, any] = field(default_factory=dict)

    # ##################################################################
    # is step completed
    # check if a step has been completed
    def is_step_completed(self, step: str) -> bool:
        return step in self.completed_steps

    # ##################################################################
    # mark step completed
    # mark a step as completed and clear error state
    def mark_step_completed(self, step: str) -> None:
        if step not in self.completed_steps:
            self.completed_steps.append(step)
        self.current_step = ""
        self.last_error = ""

    # ##################################################################
    # mark step started
    # mark a step as in progress
    def mark_step_started(self, step: str) -> None:
        self.current_step = step

    # ##################################################################
    # mark step failed
    # mark a step as failed with error message for debugging
    def mark_step_failed(self, step: str, error: str) -> None:
        self.current_step = step
        self.last_error = error

    # ##################################################################
    # get next step
    # get the next step to execute, resuming interrupted step if any
    def get_next_step(self) -> Optional[str]:
        # if there's a current step (interrupted), resume it
        if self.current_step and self.current_step not in self.completed_steps:
            return self.current_step

        # otherwise find the next incomplete step
        for step in PIPELINE_STEPS:
            if step not in self.completed_steps:
                return step
        return None

    # ##################################################################
    # get remaining steps
    # get all remaining steps that haven't been completed
    def get_remaining_steps(self) -> list[str]:
        return [s for s in PIPELINE_STEPS if s not in self.completed_steps]

    # ##################################################################
    # is complete
    # check if all pipeline steps are completed
    def is_complete(self) -> bool:
        return all(step in self.completed_steps for step in PIPELINE_STEPS)


# ##################################################################
# load state
# load project state from file, or create new state if none exists
def load_state(project_path: Path) -> ProjectState:
    state_file = project_path / STATE_FILE

    if state_file.exists():
        try:
            data = json.loads(state_file.read_text())
            state = ProjectState(**data)
            return state
        except (json.JSONDecodeError, TypeError) as e:
            # corrupted state file, start fresh but warn
            print(f"Warning: Could not load state file, starting fresh: {e}")

    # create new state
    return ProjectState(
        project_path=str(project_path),
        project_name=project_path.name,
        created_at=datetime.now().isoformat(),
    )


# ##################################################################
# save state
# persist project state to json file in project directory
def save_state(project_path: Path, state: ProjectState) -> None:
    state_file = project_path / STATE_FILE
    state.last_run = datetime.now().isoformat()

    # convert to dict, handling dataclass
    data = asdict(state)

    state_file.write_text(json.dumps(data, indent=2))


# ##################################################################
# reset state
# delete state file and return fresh state for starting over
def reset_state(project_path: Path) -> ProjectState:
    state_file = project_path / STATE_FILE
    if state_file.exists():
        state_file.unlink()
    return load_state(project_path)
