#!/usr/bin/env python3
# setup secrets for app-publish
# interactive cli tool to configure credentials in system keyring
import sys

# Read/write via /usr/bin/security (see keychain.py) so secrets stay readable
# without a GUI prompt across Homebrew Python upgrades.
import keychain

SERVICE_NAME = "app-publish"

SECRETS = [
    ("apple_id", "Apple ID (Email)"),
    ("team_id", "Apple Team ID"),
    ("bundle_id_prefix", "Bundle ID Prefix (e.g., com.yourname.)"),
    ("api_key_id", "App Store Connect API Key ID"),
    ("api_issuer_id", "App Store Connect API Issuer ID"),
    ("github_user", "GitHub Username"),
    ("contact_first_name", "Contact First Name"),
    ("contact_last_name", "Contact Last Name"),
    ("contact_email", "Contact Email"),
    ("contact_phone", "Contact Phone (International format, e.g., +15551234567)"),
    ("support_domain", "Support Domain (e.g., https://support.example.com)"),
    ("support_s3_bucket", "Support S3 Bucket Name"),
    ("support_cloudfront_id", "Support CloudFront ID"),
]


# ##################################################################
# plan secret update
# decide whether interactive input creates, retains, or leaves a value absent
def plan_secret_update(current_value: str | None, entered_value: str) -> tuple[str, str | None]:
    cleaned = entered_value.strip()
    if cleaned:
        return "update", cleaned
    if current_value:
        return "retain", current_value
    return "absent", None


# ##################################################################
# build secret prompt
# show the current value only when one is already stored
def build_secret_prompt(description: str, current_value: str | None) -> str:
    current = f" [{current_value}]" if current_value else ""
    return f"{description}{current}: "


# ##################################################################
# describe secret action
# provide a precise result message for each interactive decision
def describe_secret_action(key: str, action: str) -> str:
    if action == "update":
        return f"Updated {key}."
    if action == "retain":
        return f"Kept {key}."
    return f"No value stored for {key}."


# ##################################################################
# main
# interactive loop to prompt for and store each secret in keyring
def main() -> int:
    print(f"Setting up secrets for service: {SERVICE_NAME}")
    print("Press Enter to keep existing value (if shown in brackets).")
    print("-" * 50)

    for key, description in SECRETS:
        current_val = keychain.get_password(SERVICE_NAME, key)
        prompt = build_secret_prompt(description, current_val)

        action, value = plan_secret_update(current_val, input(prompt))

        if action == "update":
            keychain.set_password(SERVICE_NAME, key, value)
        print(describe_secret_action(key, action))

    print("-" * 50)
    print("Setup complete. Values stored in system keyring.")
    return 0


# ##################################################################
# entry point
# standard python pattern for dispatching main
if __name__ == "__main__":
    sys.exit(main())
