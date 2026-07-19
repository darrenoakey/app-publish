from setup_secrets import (
    SECRETS,
    build_secret_prompt,
    describe_secret_action,
    plan_secret_update,
)


def test_secret_setup_catalog_has_unique_machine_keys_and_human_labels() -> None:
    keys = [key for key, _ in SECRETS]
    labels = [label for _, label in SECRETS]
    assert len(keys) == len(set(keys))
    assert all(key == key.lower() and " " not in key for key in keys)
    assert all(label.strip() and label[0].isupper() for label in labels)
    assert {"apple_id", "team_id", "api_key_id", "api_issuer_id"} < set(keys)


def test_secret_update_plan_trims_new_values_and_preserves_existing_values() -> None:
    assert plan_secret_update("old", "  new  ") == ("update", "new")
    assert plan_secret_update("old", "   ") == ("retain", "old")
    assert plan_secret_update(None, "") == ("absent", None)
    assert build_secret_prompt("Team", "ABC123") == "Team [ABC123]: "
    assert build_secret_prompt("Team", None) == "Team: "
    assert describe_secret_action("team_id", "update") == "Updated team_id."
    assert describe_secret_action("team_id", "retain") == "Kept team_id."
    assert describe_secret_action("team_id", "absent") == "No value stored for team_id."
