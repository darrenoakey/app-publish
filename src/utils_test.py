import sys

from utils import (
    SECRET_FILE_ARGUMENT,
    cprint,
    dir_exists,
    ensure_dir,
    file_exists,
    find_files,
    git_add_all,
    git_commit,
    git_has_changes,
    git_init,
    git_remote_exists,
    is_git_repo,
    parse_schemes,
    pick_scheme,
    print_done,
    print_error,
    print_header,
    print_info,
    print_step,
    print_success,
    print_warning,
    read_file,
    run,
    run_check,
    run_silent,
    write_file,
    xcode_archive,
    xcode_build,
    secret_file_argument,
)


def test_process_filesystem_and_scheme_boundaries(tmp_path) -> None:
    created = ensure_dir(tmp_path / "one" / "two")
    assert created.is_dir()
    code, output = run([sys.executable, "-c", "print('process boundary ok')"])
    assert (code, output) == (0, "process boundary ok")
    listed = "Targets:\n  App\n\nSchemes:\n  Helper\n  My App UI Tests\n  My App\n\n"
    schemes = parse_schemes(listed)
    assert schemes == ["Helper", "My App UI Tests", "My App"]
    assert pick_scheme(schemes, "My-App") == "My App"


def test_process_secret_file_stays_in_memory() -> None:
    code, output = run(
        [
            sys.executable,
            "-c",
            "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())",
            SECRET_FILE_ARGUMENT,
        ],
        secret_file="process-only-test-value",
    )
    assert (code, output) == (0, "process-only-test-value")

    code, output = run(
        [
            sys.executable,
            "-c",
            "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text() + pathlib.Path(sys.argv[2]).read_text())",
            secret_file_argument(0),
            secret_file_argument(1),
        ],
        secret_files=("first-", "second"),
    )
    assert (code, output) == (0, "first-second")


def test_console_file_and_failure_boundaries(tmp_path, capsys) -> None:
    cprint("plain", "unknown")
    print_header("section")
    print_step(2, 4, "build")
    print_success("worked")
    print_error("failed")
    print_warning("careful")
    print_info("details")
    print_done("complete")
    output = capsys.readouterr().out
    assert all(
        word in output
        for word in [
            "SECTION",
            "build",
            "worked",
            "failed",
            "careful",
            "details",
            "complete",
        ]
    )

    text_path = tmp_path / "nested" / "value.txt"
    assert write_file(text_path, "value")
    assert read_file(text_path) == "value"
    assert read_file(tmp_path / "absent.txt") is None
    assert file_exists(text_path)
    assert dir_exists(text_path.parent)
    assert find_files(tmp_path, ["**/*.txt"]) == [text_path]
    assert run_silent([sys.executable, "-c", "raise SystemExit(0)"])
    assert not run_silent([sys.executable, "-c", "raise SystemExit(3)"])
    assert run_check([sys.executable, "-c", "print('checked')"]) == "checked"
    code, message = run(
        [sys.executable, "-c", "from threading import Event; Event().wait(1)"],
        timeout=0.01,
    )
    assert (code, message) == (1, "Command timed out")


def test_local_git_and_xcode_command_boundaries(tmp_path) -> None:
    assert not is_git_repo(tmp_path)
    assert git_init(tmp_path)
    assert is_git_repo(tmp_path)
    assert run_silent(["git", "config", "user.name", "App Publish Test"], cwd=tmp_path)
    assert run_silent(["git", "config", "user.email", "app-publish@example.invalid"], cwd=tmp_path)
    (tmp_path / "tracked.txt").write_text("first")
    assert git_has_changes(tmp_path)
    assert git_add_all(tmp_path)
    assert git_commit(tmp_path, "Initial local state")
    assert not git_has_changes(tmp_path)
    assert not git_remote_exists(tmp_path)
    assert run_silent(
        ["git", "remote", "add", "origin", str(tmp_path / "destination.git")],
        cwd=tmp_path,
    )
    assert git_remote_exists(tmp_path)

    built, build_output = xcode_build(tmp_path / "Absent.xcodeproj", "Absent")
    archived, archive_output = xcode_archive(tmp_path / "Absent.xcodeproj", "Absent", tmp_path / "Absent.xcarchive")
    assert not built and build_output
    assert not archived and archive_output
