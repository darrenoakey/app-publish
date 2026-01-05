#!/usr/bin/env python3
# app-publish - automated ios app store publishing pipeline
# takes any web or swift project and publishes it to the ios app store
# idempotent: safe to run multiple times
# resumable: continues from where it left off after failures
import sys
import argparse
from pathlib import Path

import setproctitle

# add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import PIPELINE_STEPS
from state import load_state, save_state, reset_state, ProjectState
from utils import (
    print_header,
    print_step,
    print_success,
    print_error,
    print_warning,
    print_info,
    print_skip,
    cprint,
)

# import step modules
from modules import (
    detect,
    structure,
    git,
    identity,
    icon,
    signing,
    build,
    screenshots,
    metadata,
    support,
    appstore,
    upload,
    submit,
    deploy,
)


# map step names to their modules
STEP_MODULES = {
    "detect": detect,
    "structure": structure,
    "git": git,
    "identity": identity,
    "icon": icon,
    "signing": signing,
    "build": build,
    "screenshots": screenshots,
    "metadata": metadata,
    "support": support,
    "appstore_create": appstore,
    "upload": upload,
    "submit": submit,
    "deploy": deploy,
}


# ##################################################################
# run step
# execute a single pipeline step with state tracking and error handling
def run_step(step: str, project_path: Path, state: ProjectState) -> bool:
    module = STEP_MODULES.get(step)
    if not module:
        print_error(f"Unknown step: {step}")
        return False

    try:
        state.mark_step_started(step)
        save_state(project_path, state)

        success = module.run(project_path, state)

        if success:
            state.mark_step_completed(step)
            save_state(project_path, state)
            return True
        else:
            state.mark_step_failed(step, "Step returned failure")
            save_state(project_path, state)
            return False

    except Exception as e:
        state.mark_step_failed(step, str(e))
        save_state(project_path, state)
        print_error(f"Step '{step}' failed with exception: {e}")
        return False


# ##################################################################
# run pipeline
# execute the complete publish pipeline with resumability support
def run_pipeline(project_path: Path, force_restart: bool = False) -> bool:
    print_header("APP-PUBLISH", "magenta")
    print_info(f"Project: {project_path}")

    # load or reset state
    if force_restart:
        print_warning("Force restart: resetting state")
        state = reset_state(project_path)
    else:
        state = load_state(project_path)

    # check if already complete
    if state.is_complete():
        print_success("All steps already completed!")
        print_info(f"App Store ID: {state.app_store_id}")
        print_info(f"Version: {state.current_version} (build {state.current_build})")
        return True

    # show status
    remaining = state.get_remaining_steps()
    completed = [s for s in PIPELINE_STEPS if s in state.completed_steps]

    if completed:
        print_info(f"Completed: {', '.join(completed)}")
    print_info(f"Remaining: {', '.join(remaining)}")

    if state.last_error:
        print_warning(f"Last error: {state.last_error}")
        print_info("Resuming from failed step...")

    # run each remaining step
    total_steps = len(PIPELINE_STEPS)
    for step in PIPELINE_STEPS:
        step_num = PIPELINE_STEPS.index(step) + 1

        if state.is_step_completed(step):
            print_skip(f"[{step_num}/{total_steps}] {step} (already done)")
            continue

        print_step(step_num, total_steps, step)

        if not run_step(step, project_path, state):
            print_error(f"Pipeline stopped at step: {step}")
            print_info("Run again to retry from this step")
            return False

        print_success(f"{step} completed")

    # all done
    print_header("SUCCESS!", "green")
    print_success("App published successfully!")
    print_info(f"Bundle ID: {state.bundle_id}")
    print_info(f"App Store ID: {state.app_store_id}")
    print_info(f"Version: {state.current_version} (build {state.current_build})")

    return True


# ##################################################################
# main
# cli entry point that parses arguments and dispatches to appropriate handler
def main() -> int:
    setproctitle.setproctitle("app-publish")

    parser = argparse.ArgumentParser(
        description="Publish an iOS app to the App Store",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  app-publish                  # Publish current directory
  app-publish ~/src/my-app     # Publish specific project
  app-publish --restart        # Start over from scratch
  app-publish --status         # Show current status
  app-publish --step detect    # Run only the detect step
        """,
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Project directory to publish (default: current directory)",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Force restart from beginning (clears state)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current pipeline status without running",
    )
    parser.add_argument(
        "--step",
        choices=PIPELINE_STEPS,
        help="Run only a specific step",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="app-publish 0.1.0",
    )
    parser.add_argument(
        "--deploy",
        nargs="?",
        const="Starbuck",
        metavar="DEVICE",
        help="Deploy to iOS device (default: Starbuck)",
    )

    args = parser.parse_args()

    # resolve project path
    project_path = Path(args.project).expanduser().resolve()

    if not project_path.exists():
        print_error(f"Project path does not exist: {project_path}")
        return 1

    if not project_path.is_dir():
        print_error(f"Project path is not a directory: {project_path}")
        return 1

    # status only
    if args.status:
        state = load_state(project_path)
        print_header("PIPELINE STATUS", "cyan")
        print_info(f"Project: {project_path}")
        print_info(f"Type: {state.project_type or 'not detected'}")
        print_info(f"Bundle ID: {state.bundle_id or 'not set'}")
        print_info(f"App Name: {state.app_name or 'not set'}")
        print_info(f"Version: {state.current_version} (build {state.current_build})")
        print()
        for step in PIPELINE_STEPS:
            status = "[X]" if step in state.completed_steps else "[ ]"
            current = " <-- current" if step == state.current_step else ""
            cprint(f"  {status} {step}{current}", "green" if status == "[X]" else "white")
        if state.last_error:
            print()
            print_warning(f"Last error: {state.last_error}")
        return 0

    # single step mode
    if args.step:
        state = load_state(project_path)
        print_header(f"RUNNING STEP: {args.step}", "cyan")
        success = run_step(args.step, project_path, state)
        return 0 if success else 1

    # deploy mode
    if args.deploy:
        state = load_state(project_path)
        device_name = args.deploy
        print_header(f"DEPLOYING TO: {device_name}", "cyan")
        success = deploy.run(project_path, state, device_name)
        return 0 if success else 1

    # full pipeline
    success = run_pipeline(project_path, force_restart=args.restart)
    return 0 if success else 1


# ##################################################################
# entry point
# standard python pattern for dispatching main
if __name__ == "__main__":
    sys.exit(main())
