#!/usr/bin/env python3
# setup secrets for app-publish
# interactive cli tool to configure credentials in system keyring
import sys
try:
    import keyring
except ImportError:
    print("Error: 'keyring' module not found. Please run 'pip install -r requirements.txt'")
    sys.exit(1)

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
# main
# interactive loop to prompt for and store each secret in keyring
def main() -> int:
    print(f"Setting up secrets for service: {SERVICE_NAME}")
    print("Press Enter to keep existing value (if shown in brackets).")
    print("-" * 50)

    for key, description in SECRETS:
        current_val = keyring.get_password(SERVICE_NAME, key)
        prompt = f"{description}"
        if current_val:
            prompt += f" [{current_val}]"
        prompt += ": "

        new_val = input(prompt).strip()

        if new_val:
            keyring.set_password(SERVICE_NAME, key, new_val)
            print(f"Updated {key}.")
        elif current_val:
            print(f"Kept {key}.")
        else:
            print(f"Skipped {key} (no value set).")

    print("-" * 50)
    print("Setup complete. Values stored in system keyring.")
    return 0


# ##################################################################
# entry point
# standard python pattern for dispatching main
if __name__ == "__main__":
    sys.exit(main())
