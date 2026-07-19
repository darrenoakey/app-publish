import importlib

import modules.identity as identity_module
from modules.identity import gather_project_context, save_metadata_files
from state import ProjectState

importlib.reload(identity_module)


def test_identity_context_and_metadata_files_respect_limits(tmp_path) -> None:
    (tmp_path / "README.md").write_text("A focused reading application")
    (tmp_path / "index.html").write_text("<main>Reader</main>")
    state = ProjectState(project_name="reader", project_type="web")
    context = gather_project_context(tmp_path, state)
    assert "Project name: reader" in context
    assert "A focused reading application" in context
    identity = {
        "app_name": "Reader",
        "subtitle": "Read calmly",
        "description": "A calm reader.",
        "keywords": ["word" * 20, "second", "third"],
        "promotional_text": "Start reading",
    }
    save_metadata_files(tmp_path, identity)
    metadata = tmp_path / "fastlane" / "metadata" / "en-US"
    assert (metadata / "name.txt").read_text() == "Reader"
    assert len((metadata / "keywords.txt").read_text()) <= 100
    assert (metadata / "release_notes.txt").read_text() == "Initial release"
