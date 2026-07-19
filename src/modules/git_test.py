import importlib

import modules.git as git_module
from modules.git import create_gitignore, initialize_local_repository, run
from state import ProjectState

importlib.reload(git_module)


def test_gitignore_creation_is_additive_and_idempotent(tmp_path) -> None:
    target = tmp_path / ".gitignore"
    target.write_text("custom-entry\n")
    create_gitignore(tmp_path, "web")
    first = target.read_text()
    create_gitignore(tmp_path, "web")
    assert target.read_text() == first
    assert "custom-entry" in first
    assert "# app-publish managed" in first
    assert "node_modules/" in first
    assert "\n.build/\n" not in first


def test_gitignore_creation_supports_new_swift_projects(tmp_path) -> None:
    create_gitignore(tmp_path, "swift")
    content = (tmp_path / ".gitignore").read_text()
    assert content.startswith("# app-publish managed")
    assert "\n.build/\n" in content
    assert "node_modules/" not in content


def test_git_setup_stops_when_real_local_repository_initialization_fails(tmp_path, capsys) -> None:
    invalid_repository = tmp_path / "not-a-directory"
    invalid_repository.write_text("local file")
    assert run(invalid_repository, ProjectState(project_type="swift")) is False
    assert "Failed to initialize git repository" in capsys.readouterr().out


def test_local_repository_initialization_uses_real_git(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    assert initialize_local_repository(repository) is True
    assert (repository / ".git").is_dir()
    assert initialize_local_repository(repository) is True
