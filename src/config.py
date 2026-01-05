# configuration constants for app-publish
# centralized secrets and configuration values
from pathlib import Path
import sys

try:
    import keyring
except ImportError:
    print("Error: 'keyring' module not found. Please run 'pip install -r requirements.txt'")
    sys.exit(1)

SERVICE_NAME = "app-publish"

# track missing secrets for validation
_missing_secrets: list[str] = []


# ##################################################################
# get secret
# retrieve a secret from the system keyring for app-publish service
def get_secret(key: str, required: bool = True) -> str | None:
    val = keyring.get_password(SERVICE_NAME, key)
    if not val and required:
        _missing_secrets.append(key)
    return val


# required secrets - these are loaded and validated at startup
# apple developer account
TEAM_ID = get_secret("team_id")
APPLE_ID = get_secret("apple_id")
BUNDLE_ID_PREFIX = get_secret("bundle_id_prefix")

# app store connect api
API_KEY_ID = get_secret("api_key_id")
API_ISSUER_ID = get_secret("api_issuer_id")
API_KEY_PATH = Path.home() / ".appstoreconnect" / "private_keys" / f"AuthKey_{API_KEY_ID}.p8"

# github
GITHUB_USER = get_secret("github_user")
CERTIFICATE_REPO = f"{GITHUB_USER}/app_store_certificates" if GITHUB_USER else None

# contact info
CONTACT_FIRST_NAME = get_secret("contact_first_name")
CONTACT_LAST_NAME = get_secret("contact_last_name")
CONTACT_EMAIL = get_secret("contact_email")
CONTACT_PHONE = get_secret("contact_phone")

# paths
APP_PUBLISH_DIR = Path(__file__).parent.resolve()
SRC_ROOT = APP_PUBLISH_DIR.parent

# state file
STATE_FILE = ".app-publish.json"

# asset dimensions
ICON_SIZE = 1024  # master icon size
ICON_SIZES_IOS = [
    # (size, scale, idiom, filename_suffix)
    (20, 1, "ipad", "ipad-20x20@1x"),
    (20, 2, "iphone", "iphone-20x20@2x"),
    (20, 2, "ipad", "ipad-20x20@2x"),
    (20, 3, "iphone", "iphone-20x20@3x"),
    (29, 1, "ipad", "ipad-29x29@1x"),
    (29, 2, "iphone", "iphone-29x29@2x"),
    (29, 2, "ipad", "ipad-29x29@2x"),
    (29, 3, "iphone", "iphone-29x29@3x"),
    (40, 1, "ipad", "ipad-40x40@1x"),
    (40, 2, "iphone", "iphone-40x40@2x"),
    (40, 2, "ipad", "ipad-40x40@2x"),
    (40, 3, "iphone", "iphone-40x40@3x"),
    (60, 2, "iphone", "iphone-60x60@2x"),
    (60, 3, "iphone", "iphone-60x60@3x"),
    (76, 1, "ipad", "ipad-76x76@1x"),
    (76, 2, "ipad", "ipad-76x76@2x"),
    (83.5, 2, "ipad", "ipad-83.5x83.5@2x"),
    (1024, 1, "ios-marketing", "ios-marketing-1024x1024@1x"),
]

# screenshot device sizes (required by app store)
SCREENSHOT_DEVICES = [
    "iPhone 16 Pro Max",  # 6.7" - 1290x2796
    "iPhone 16 Plus",     # 6.5" - 1284x2778 (alternative 6.5")
    "iPad Pro 13-inch (M4)",  # 12.9" - 2048x2732
    "iPad Pro 11-inch (M4)",  # 11" - 1668x2388
]

# pipeline steps (in order)
PIPELINE_STEPS = [
    "detect",
    "structure",
    "git",
    "identity",
    "icon",
    "signing",
    "build",
    "screenshots",
    "metadata",
    "support",      # create/update support page
    "appstore_create",
    "upload",
    "submit",
]

# support page configuration
SUPPORT_DOMAIN = get_secret("support_domain")
SUPPORT_S3_BUCKET = get_secret("support_s3_bucket")
SUPPORT_CLOUDFRONT_ID = get_secret("support_cloudfront_id")
COMPANY_NAME = get_secret("company_name")
SUPPORT_EMAIL_PREFIX = get_secret("support_email_prefix", required=False) or "support_"
SUPPORT_EMAIL_DOMAIN = get_secret("support_email_domain")

# web tech indicators (files that suggest web-based project)
WEB_INDICATORS = [
    "index.html",
    "package.json",
    "vite.config.js",
    "vite.config.ts",
    "webpack.config.js",
    "next.config.js",
    "nuxt.config.js",
]

# swift/native indicators
SWIFT_INDICATORS = [
    "*.xcodeproj",
    "*.xcworkspace",
    "Package.swift",
    "*.swift",
]


# ##################################################################
# validate secrets
# check that all required secrets are present in keyring
def _validate_secrets() -> None:
    if _missing_secrets:
        print("Error: Missing required secrets in keyring.")
        print(f"Service name: {SERVICE_NAME}")
        print("\nMissing secrets:")
        for key in _missing_secrets:
            print(f"  - {key}")
        print("\nTo add a secret, run:")
        print(f"  python3 -c \"import keyring; keyring.set_password('{SERVICE_NAME}', '<key>', '<value>')\"")
        print("\nExample:")
        print(f"  python3 -c \"import keyring; keyring.set_password('{SERVICE_NAME}', '{_missing_secrets[0]}', 'your_value_here')\"")
        sys.exit(1)


_validate_secrets()
