import importlib

import modules.metadata as metadata_module
from modules.metadata import generate_age_rating_answers, generate_review_info, run
from state import ProjectState

importlib.reload(metadata_module)


def test_review_metadata_and_age_answers_are_complete() -> None:
    state = ProjectState(app_name="Calm Reader")
    ratings = generate_age_rating_answers(state)
    assert len(ratings) == 13
    assert set(ratings.values()) == {"NONE"}
    review = generate_review_info(state)
    assert "Calm Reader" in review["notes"]
    assert review["demo_account_name"] == ""
    assert review["demo_account_password"] == ""


def test_metadata_generation_persists_complete_real_local_files(tmp_path) -> None:
    metadata = tmp_path / "fastlane" / "metadata" / "en-US"
    metadata.mkdir(parents=True)
    for name, content in {
        "name.txt": "Calm Reader",
        "description.txt": "Read without distractions",
        "keywords.txt": "reading,calm",
    }.items():
        (metadata / name).write_text(content)
    state = ProjectState(app_name="Calm Reader")
    policy = "Privacy Policy\n\nNo personal information is collected."
    assert run(tmp_path, state, policy) is True
    assert (tmp_path / "PRIVACY_POLICY.md").read_text() == policy
    assert (metadata / "marketing_url.txt").read_text() == ""
    assert "Copyright" in (metadata / "copyright.txt").read_text()
    review = metadata / "review_information"
    assert (review / "notes.txt").read_text() == state.metadata["review_info"]["notes"]
    assert (review / "email_address.txt").read_text() == state.metadata["review_info"]["contact_email"]
    assert len(state.metadata["age_rating"]) == 13


def test_metadata_generation_requires_identity_files(tmp_path, capsys) -> None:
    assert run(tmp_path, ProjectState(), "Local policy") is False
    assert "Run identity step first" in capsys.readouterr().out
