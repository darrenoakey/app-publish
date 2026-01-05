from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState, load_state, save_state
from config import CONTACT_FIRST_NAME, CONTACT_LAST_NAME, CONTACT_EMAIL, CONTACT_PHONE
from utils import (
    print_info,
    print_success,
    print_warning,
    llm_json,
    ensure_dir,
    write_file,
    file_exists,
)


# ##################################################################
# generate privacy policy
# generate a privacy policy for the app
def generate_privacy_policy(state: ProjectState) -> str:
    prompt = f"""Generate a simple, clear privacy policy for an iOS app called "{state.app_name}".

App description: {state.app_description[:300] if state.app_description else 'A mobile application'}

The privacy policy should:
- Be suitable for a simple app that doesn't collect personal data
- State that no personal information is collected or shared
- Be written in plain English
- Be about 300-400 words

Respond with ONLY the privacy policy text, no other formatting.
"""
    from utils import llm_chat
    policy = llm_chat(prompt)
    if not policy:
        return f"""Privacy Policy for {state.app_name}

Last updated: {__import__('datetime').datetime.now().strftime('%B %d, %Y')}

This privacy policy describes how {state.app_name} ("we", "our", or "the app") handles information.

Information Collection
We do not collect, store, or share any personal information. The app functions entirely on your device without requiring an account or transmitting data to external servers.

Data Storage
Any data you enter into the app is stored locally on your device. We do not have access to this information.

Third-Party Services
This app does not integrate with third-party analytics, advertising, or tracking services.

Children's Privacy
This app does not knowingly collect information from children under 13.

Changes to This Policy
We may update this privacy policy from time to time. Any changes will be reflected in the app.

Contact
If you have questions about this privacy policy, please contact us through the App Store.
"""
    return policy
# ##################################################################
# generate privacy policy
# generate a privacy policy for the app


# ##################################################################
# generate age rating answers
# generate age rating questionnaire answers
def generate_age_rating_answers(state: ProjectState) -> dict[str, str]:
    # default to the most restrictive (safest) answers
    # these indicate no objectionable content
    return {
        "CARTOON_FANTASY_VIOLENCE": "NONE",
        "REALISTIC_VIOLENCE": "NONE",
        "PROLONGED_GRAPHIC_SADISTIC_REALISTIC_VIOLENCE": "NONE",
        "PROFANITY_CRUDE_HUMOR": "NONE",
        "MATURE_SUGGESTIVE": "NONE",
        "HORROR": "NONE",
        "MEDICAL_TREATMENT_INFO": "NONE",
        "ALCOHOL_TOBACCO_DRUGS": "NONE",
        "GAMBLING": "NONE",
        "SEXUAL_CONTENT_NUDITY": "NONE",
        "GRAPHIC_SEXUAL_CONTENT_NUDITY": "NONE",
        "UNRESTRICTED_WEB_ACCESS": "NONE",
        "GAMBLING_CONTESTS": "NONE",
    }
# ##################################################################
# generate age rating answers
# generate age rating questionnaire answers


# ##################################################################
# generate review info
# generate app review information
def generate_review_info(state: ProjectState) -> dict[str, str]:
    return {
        "contact_email": CONTACT_EMAIL,
        "contact_phone": CONTACT_PHONE,  # no spaces - api requires clean format
        "contact_first_name": CONTACT_FIRST_NAME,
        "contact_last_name": CONTACT_LAST_NAME,
        "demo_account_name": "",
        "demo_account_password": "",
        "notes": f"""Thank you for reviewing {state.app_name}.

This app is straightforward to use - simply launch it and explore the interface.

No login or account is required. All functionality is available immediately.

If you have any questions during the review, please don't hesitate to contact us.
""",
    }
# ##################################################################
# generate review info
# generate app review information


# ##################################################################
# run
# run metadata step
# creates privacy policy, review information, and age rating configuration
def run(project_path: Path, state: ProjectState) -> bool:
    metadata_dir = project_path / "fastlane" / "metadata" / "en-US"
    ensure_dir(metadata_dir)

    # check existing metadata
    required_files = ["name.txt", "description.txt", "keywords.txt"]
    missing = [f for f in required_files if not file_exists(metadata_dir / f)]
    if missing:
        print_warning(f"Missing metadata files: {missing}")
        print_info("Run identity step first")
        return False

    # generate privacy policy markdown (actual URLs set by support module)
    print_info("Generating privacy policy...")
    privacy_policy = generate_privacy_policy(state)
    write_file(project_path / "PRIVACY_POLICY.md", privacy_policy)
    print_success("Privacy policy created")

    # marketing url (optional)
    write_file(metadata_dir / "marketing_url.txt", "")

    # copyright
    import datetime
    year = datetime.datetime.now().year
    write_file(metadata_dir / "copyright.txt", f"Copyright {year} {CONTACT_FIRST_NAME} {CONTACT_LAST_NAME}")

    # age rating
    print_info("Configuring age rating...")
    # fastlane uses a different format - we'll handle this in the upload step
    state.metadata["age_rating"] = generate_age_rating_answers(state)
    print_success("Age rating configured (4+)")

    # review information
    print_info("Generating review information...")
    review_info = generate_review_info(state)
    state.metadata["review_info"] = review_info

    # create review_information directory
    review_dir = metadata_dir / "review_information"
    ensure_dir(review_dir)
    write_file(review_dir / "notes.txt", review_info["notes"])
    write_file(review_dir / "email_address.txt", review_info["contact_email"])
    write_file(review_dir / "phone_number.txt", review_info["contact_phone"])
    write_file(review_dir / "first_name.txt", review_info["contact_first_name"])
    write_file(review_dir / "last_name.txt", review_info["contact_last_name"])

    print_success("All metadata generated")

    return True
# ##################################################################
# run
# run metadata step
# creates privacy policy, review information, and age rating configuration


if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) < 2:
        print("Usage: python metadata.py <project_path>")
        _sys.exit(1)

    project_path = Path(_sys.argv[1]).resolve()
    state = load_state(project_path)

    if not state.bundle_id:
        print_warning("No bundle_id in state - run structure step first")
        _sys.exit(1)

    from utils import print_error
    success = run(project_path, state)
    if success:
        save_state(project_path, state)
        print_success("Metadata step completed successfully!")
    else:
        print_error("Metadata step failed!")
        _sys.exit(1)
