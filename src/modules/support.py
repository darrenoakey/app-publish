import subprocess
from pathlib import Path
from html import escape

from config import (
    SUPPORT_DOMAIN,
    SUPPORT_S3_BUCKET,
    SUPPORT_CLOUDFRONT_ID,
    COMPANY_NAME,
    SUPPORT_EMAIL_PREFIX,
    SUPPORT_EMAIL_DOMAIN,
)
from utils import print_info, print_success, print_warning, print_error, write_file


# ##################################################################
# generate support html
# generate static html support page for an app
def generate_support_html(app_name: str, app_id: str, icon_path: Path = None) -> str:
    # convert app name to email-safe format (lowercase, underscores)
    email_name = app_name.lower().replace(" ", "_").replace("-", "_")
    support_email = f"{SUPPORT_EMAIL_PREFIX}{email_name}@{SUPPORT_EMAIL_DOMAIN}"

    # escape for html safety
    safe_name = escape(app_name)
    safe_email = escape(support_email)

    # app store link if we have an id
    app_store_link = ""
    if app_id:
        app_store_link = f'''
        <a href="https://apps.apple.com/app/id{escape(app_id)}" class="app-store-badge">
            <img src="https://tools.applemediaservices.com/api/badges/download-on-the-app-store/black/en-us?size=250x83"
                 alt="Download on the App Store"
                 width="200">
        </a>'''

    # icon section - use app icon if available, otherwise a placeholder
    icon_section = f'''
        <div class="app-icon">
            <img src="icon.png" alt="{safe_name}" width="180" height="180">
        </div>'''

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_name} - Support</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}

        .container {{
            background: white;
            border-radius: 24px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            max-width: 500px;
            width: 100%;
            padding: 48px 40px;
            text-align: center;
        }}

        .app-icon {{
            margin-bottom: 24px;
        }}

        .app-icon img {{
            border-radius: 36px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
        }}

        h1 {{
            font-size: 2rem;
            font-weight: 700;
            color: #1a1a2e;
            margin-bottom: 8px;
        }}

        .tagline {{
            color: #666;
            font-size: 1.1rem;
            margin-bottom: 32px;
        }}

        .support-section {{
            background: #f8f9fa;
            border-radius: 16px;
            padding: 28px;
            margin-bottom: 24px;
        }}

        .support-section h2 {{
            font-size: 1.1rem;
            color: #333;
            margin-bottom: 16px;
            font-weight: 600;
        }}

        .support-section p {{
            color: #555;
            line-height: 1.6;
            margin-bottom: 16px;
        }}

        .email-link {{
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            text-decoration: none;
            padding: 14px 28px;
            border-radius: 12px;
            font-weight: 600;
            font-size: 1rem;
            transition: transform 0.2s, box-shadow 0.2s;
        }}

        .email-link:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4);
        }}

        .app-store-badge {{
            display: inline-block;
            margin-top: 8px;
        }}

        .app-store-badge img {{
            height: 50px;
            width: auto;
        }}

        .footer {{
            margin-top: 24px;
            color: #999;
            font-size: 0.85rem;
        }}

        .footer a {{
            color: #667eea;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="container">
        {icon_section}

        <h1>{safe_name}</h1>
        <p class="tagline">iOS Application</p>

        <div class="support-section">
            <h2>Need Help?</h2>
            <p>
                We're here to help! If you have questions, feedback, or need assistance
                with {safe_name}, please don't hesitate to reach out.
            </p>
            <a href="mailto:{safe_email}" class="email-link">
                Contact Support
            </a>
        </div>

        {app_store_link}

        <div class="footer">
            <p>&copy; 2024 {escape(COMPANY_NAME)}. All rights reserved.</p>
        </div>
    </div>
</body>
</html>'''

    return html
# ##################################################################
# generate support html
# generate static html support page for an app


# ##################################################################
# generate 404 html
# generate a simple 404 page
def generate_404_html() -> str:
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Page Not Found</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            margin: 0;
        }
        .container {
            background: white;
            border-radius: 24px;
            padding: 48px;
            text-align: center;
            max-width: 400px;
        }
        h1 { font-size: 4rem; color: #667eea; margin-bottom: 16px; }
        p { color: #666; font-size: 1.1rem; }
        a { color: #667eea; text-decoration: none; font-weight: 600; }
    </style>
</head>
<body>
    <div class="container">
        <h1>404</h1>
        <p>The page you're looking for doesn't exist.</p>
        <p><a href="/">Return Home</a></p>
    </div>
</body>
</html>'''
# ##################################################################
# generate 404 html
# generate a simple 404 page


# ##################################################################
# list apps from s3
# list all app directories from s3 bucket
# returns list of dicts with slug and display name
def list_apps_from_s3() -> list[dict[str, str]]:
    apps = []
    try:
        # list all prefixes (directories) in the bucket
        cmd = ["aws", "s3", "ls", f"s3://{SUPPORT_S3_BUCKET}/"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if line.strip() and 'PRE ' in line:
                    # parse "PRE app-name/" format
                    slug = line.split('PRE ')[-1].strip().rstrip('/')
                    # skip system files and non-app directories
                    if slug and not slug.startswith('.') and slug not in ('404', 'index'):
                        # convert slug to display name (capitalize words)
                        display_name = ' '.join(word.capitalize() for word in slug.split('-'))
                        apps.append({'slug': slug, 'name': display_name})
    except Exception as e:
        print_warning(f"Could not list apps from S3: {e}")
    return sorted(apps, key=lambda x: x['name'])
# ##################################################################
# list apps from s3
# list all app directories from s3 bucket


# ##################################################################
# generate index html
# generate the root index page listing all apps
def generate_index_html(apps: list[dict[str, str]] = None) -> str:
    from datetime import datetime
    current_year = datetime.now().year

    if apps is None:
        apps = list_apps_from_s3()

    # generate app cards html
    if apps:
        app_cards = '\n'.join(f'''
            <a href="/{app['slug']}/" class="app-card">
                <img src="/{app['slug']}/icon.png"
                     alt="{escape(app['name'])}"
                     class="app-icon"
                     onerror="this.style.display='none'">
                <span class="app-name">{escape(app['name'])}</span>
            </a>''' for app in apps)
        apps_section = f'<div class="apps-grid">{app_cards}</div>'
    else:
        apps_section = '<p class="no-apps">No apps published yet.</p>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>iOS Apps - {escape(COMPANY_NAME)}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            margin: 0;
            padding: 40px 20px;
        }}

        .container {{
            background: white;
            border-radius: 24px;
            padding: 48px;
            text-align: center;
            max-width: 800px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }}

        h1 {{
            color: #1a1a2e;
            margin-bottom: 8px;
            font-size: 2.2rem;
        }}

        .subtitle {{
            color: #666;
            line-height: 1.6;
            margin-bottom: 32px;
            font-size: 1.1rem;
        }}

        .apps-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
            gap: 24px;
            margin-top: 24px;
        }}

        .app-card {{
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 20px 16px;
            background: #f8f9fa;
            border-radius: 16px;
            text-decoration: none;
            transition: transform 0.2s, box-shadow 0.2s;
        }}

        .app-card:hover {{
            transform: translateY(-4px);
            box-shadow: 0 8px 24px rgba(102, 126, 234, 0.25);
        }}

        .app-icon {{
            width: 80px;
            height: 80px;
            border-radius: 18px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
            margin-bottom: 12px;
        }}

        .app-name {{
            color: #333;
            font-weight: 600;
            font-size: 0.95rem;
            text-align: center;
            line-height: 1.3;
        }}

        .no-apps {{
            color: #999;
            font-style: italic;
            padding: 40px;
        }}

        .footer {{
            margin-top: 40px;
            padding-top: 24px;
            border-top: 1px solid #eee;
            color: #999;
            font-size: 0.85rem;
        }}

        @media (max-width: 480px) {{
            .apps-grid {{
                grid-template-columns: repeat(2, 1fr);
                gap: 16px;
            }}
            .container {{
                padding: 32px 24px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>iOS Apps</h1>
        <p class="subtitle">Support pages for iOS applications by {escape(COMPANY_NAME)}</p>

        {apps_section}

        <div class="footer">
            <p>&copy; {current_year} {escape(COMPANY_NAME)}. All rights reserved.</p>
        </div>
    </div>
</body>
</html>'''
# ##################################################################
# generate index html
# generate the root index page listing all apps


# ##################################################################
# upload to s3
# upload a file to s3
def upload_to_s3(local_path: Path, s3_key: str, content_type: str = "text/html") -> bool:
    try:
        cmd = [
            "aws", "s3", "cp", str(local_path),
            f"s3://{SUPPORT_S3_BUCKET}/{s3_key}",
            "--content-type", content_type,
            "--cache-control", "max-age=3600"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    except Exception as e:
        print_error(f"S3 upload failed: {e}")
        return False
# ##################################################################
# upload to s3
# upload a file to s3


# ##################################################################
# upload string to s3
# upload a string directly to s3
def upload_string_to_s3(content: str, s3_key: str, content_type: str = "text/html") -> bool:
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write(content)
            temp_path = Path(f.name)

        success = upload_to_s3(temp_path, s3_key, content_type)
        temp_path.unlink()
        return success
    except Exception as e:
        print_error(f"S3 upload failed: {e}")
        return False
# ##################################################################
# upload string to s3
# upload a string directly to s3


# ##################################################################
# generate privacy policy html
# generate a privacy policy html page for an app
def generate_privacy_policy_html(app_name: str, app_description: str = "") -> str:
    from datetime import datetime
    safe_name = escape(app_name)
    current_date = datetime.now().strftime("%B %d, %Y")

    # generate privacy policy content based on app
    # for most simple apps that don't collect data
    policy_content = f"""
        <h2>Privacy Policy for {safe_name}</h2>
        <p class="date">Last updated: {current_date}</p>

        <section>
            <h3>Introduction</h3>
            <p>{safe_name} ("we", "our", or "the app") is committed to protecting your privacy.
            This privacy policy explains how we handle information when you use our iOS application.</p>
        </section>

        <section>
            <h3>Information Collection</h3>
            <p>We do not collect, store, or share any personal information. {safe_name} operates
            entirely on your device without requiring an account, login, or transmitting data to external servers.</p>
        </section>

        <section>
            <h3>Data Storage</h3>
            <p>Any data you create or enter into the app is stored locally on your device only.
            We do not have access to this information, and it is not transmitted anywhere.</p>
        </section>

        <section>
            <h3>Third-Party Services</h3>
            <p>This app does not integrate with third-party analytics, advertising, tracking services,
            or social media platforms. Your usage of the app remains completely private.</p>
        </section>

        <section>
            <h3>Children's Privacy</h3>
            <p>This app does not knowingly collect any information from children under the age of 13.
            The app is suitable for users of all ages.</p>
        </section>

        <section>
            <h3>Changes to This Policy</h3>
            <p>We may update this privacy policy from time to time. Any changes will be reflected
            on this page with an updated revision date.</p>
        </section>

        <section>
            <h3>Contact Us</h3>
            <p>If you have any questions about this privacy policy, please contact us through
            the App Store or via our support page.</p>
        </section>
    """

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_name} - Privacy Policy</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 40px 20px;
        }}

        .container {{
            background: white;
            border-radius: 24px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            max-width: 700px;
            margin: 0 auto;
            padding: 48px 40px;
        }}

        h2 {{
            font-size: 1.8rem;
            font-weight: 700;
            color: #1a1a2e;
            margin-bottom: 8px;
            text-align: center;
        }}

        .date {{
            text-align: center;
            color: #666;
            margin-bottom: 32px;
            font-size: 0.9rem;
        }}

        section {{
            margin-bottom: 24px;
        }}

        h3 {{
            font-size: 1.1rem;
            font-weight: 600;
            color: #333;
            margin-bottom: 12px;
            padding-bottom: 8px;
            border-bottom: 2px solid #667eea;
        }}

        p {{
            color: #555;
            line-height: 1.7;
            margin-bottom: 12px;
        }}

        .footer {{
            margin-top: 32px;
            text-align: center;
            color: #999;
            font-size: 0.85rem;
        }}

        .footer a {{
            color: #667eea;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="container">
        {policy_content}
        <div class="footer">
            <p>&copy; {datetime.now().year} {escape(COMPANY_NAME)}. All rights reserved.</p>
            <p><a href="./">Back to {safe_name}</a></p>
        </div>
    </div>
</body>
</html>'''

    return html
# ##################################################################
# generate privacy policy html
# generate a privacy policy html page for an app


# ##################################################################
# invalidate cloudfront
# invalidate cloudfront cache for updated paths
def invalidate_cloudfront(paths: list[str] = None) -> bool:
    if paths is None:
        paths = ["/*"]

    try:
        cmd = [
            "aws", "cloudfront", "create-invalidation",
            "--distribution-id", SUPPORT_CLOUDFRONT_ID,
            "--paths", *paths
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    except Exception as e:
        print_warning(f"CloudFront invalidation failed: {e}")
        return False
# ##################################################################
# invalidate cloudfront
# invalidate cloudfront cache for updated paths


# ##################################################################
# run
# create/update support page for the app
# creates /{app-slug}/index.html, /{app-slug}/icon.png, /index.html, /404.html
def run(project_path: Path, state) -> bool:
    print_info("Creating support page...")

    app_name = state.app_name
    app_id = state.app_store_id

    if not app_name:
        print_warning("No app name set, skipping support page")
        return True

    # create url-safe slug from app name
    slug = app_name.lower().replace(" ", "-").replace("_", "-")
    # remove any non-alphanumeric characters except hyphens
    slug = "".join(c for c in slug if c.isalnum() or c == "-")

    print_info(f"App: {app_name}")
    print_info(f"Slug: {slug}")
    print_info(f"Support URL: {SUPPORT_DOMAIN}/{slug}/")

    # generate and upload support page
    html = generate_support_html(app_name, app_id)

    if not upload_string_to_s3(html, f"{slug}/index.html"):
        print_error("Failed to upload support page")
        return False
    print_success(f"Uploaded {slug}/index.html")

    # generate and upload privacy policy
    print_info("Generating privacy policy...")
    app_description = state.app_description if hasattr(state, 'app_description') else ""
    privacy_html = generate_privacy_policy_html(app_name, app_description)

    if not upload_string_to_s3(privacy_html, f"{slug}/privacy.html"):
        print_warning("Failed to upload privacy policy")
    else:
        print_success(f"Uploaded {slug}/privacy.html")

    # upload app icon if available
    icon_path = project_path / "ios" / "App" / "App" / "Assets.xcassets" / "AppIcon.appiconset" / "ios-marketing-1024x1024@1x.png"
    if not icon_path.exists():
        # try alternative location
        icon_path = project_path / "AppIcon.png"

    if icon_path.exists():
        # resize to 180x180 for web display
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            temp_icon = Path(f.name)

        try:
            resize_cmd = ["sips", "-z", "180", "180", str(icon_path), "--out", str(temp_icon)]
            subprocess.run(resize_cmd, capture_output=True)

            if upload_to_s3(temp_icon, f"{slug}/icon.png", "image/png"):
                print_success(f"Uploaded {slug}/icon.png")
            temp_icon.unlink()
        except Exception as e:
            print_warning(f"Could not process icon: {e}")

    # upload root index and 404 (only if they don't exist or we're updating)
    upload_string_to_s3(generate_index_html(), "index.html")
    upload_string_to_s3(generate_404_html(), "404.html")

    # invalidate cloudfront cache
    invalidate_cloudfront([f"/{slug}/*", "/index.html", "/404.html"])

    # store the urls in state (use clean url - cloudfront function handles index.html)
    support_url = f"{SUPPORT_DOMAIN}/{slug}/"
    privacy_url = f"{SUPPORT_DOMAIN}/{slug}/privacy.html"
    state.metadata["support_url"] = support_url
    state.metadata["privacy_url"] = privacy_url

    # update metadata files with the urls
    metadata_dir = project_path / "fastlane" / "metadata" / "en-US"
    if metadata_dir.exists():
        write_file(metadata_dir / "support_url.txt", support_url)
        write_file(metadata_dir / "privacy_url.txt", privacy_url)
        print_info(f"Updated metadata/en-US/support_url.txt")
        print_info(f"Updated metadata/en-US/privacy_url.txt")

        # also update en-au if it exists
        en_au_dir = project_path / "fastlane" / "metadata" / "en-AU"
        if en_au_dir.exists():
            write_file(en_au_dir / "support_url.txt", support_url)
            write_file(en_au_dir / "privacy_url.txt", privacy_url)
            print_info(f"Updated metadata/en-AU/support_url.txt")
            print_info(f"Updated metadata/en-AU/privacy_url.txt")

    print_success(f"Support page ready: {support_url}")
    print_success(f"Privacy policy ready: {privacy_url}")

    return True
# ##################################################################
# run
# create/update support page for the app
